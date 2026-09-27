#!/usr/bin/env python3
"""Verify the image, support RPM, and published rootfs manifest agree."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


VERSION_RE = re.compile(r"^(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})$")
PORT_FILE = "/usr/share/tab-companion/port.json"
HI1337_TUNING_FILE = "/usr/share/libcamera/ipa/simple/hi1337-gts9u.yaml"
PACKAGE_NAME = "x810-fedora-port"
REPO_ROOT = Path(__file__).resolve().parents[1]


def fail(message):
    raise ValueError(message)


def parse_manifest(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in values:
            fail(f"duplicate manifest field: {key}")
        values[key] = value
    return values


def rpm_query(rpm_path, query):
    return subprocess.check_output(
        ["rpm", "-qp", "--qf", query, str(rpm_path)], text=True
    ).strip()


def check(rootfs, manifest_path, version, rpm_path=None):
    rootfs = Path(rootfs)
    tuning_overlay = REPO_ROOT / "rootfs/overlay" / HI1337_TUNING_FILE.lstrip("/")
    tuning_image = rootfs / HI1337_TUNING_FILE.lstrip("/")
    if not tuning_overlay.is_file() or tuning_overlay.is_symlink():
        fail("source overlay is missing the HI1337 IPA tuning YAML")
    if not tuning_image.is_file() or tuning_image.is_symlink():
        fail("fresh rootfs is missing the HI1337 IPA tuning YAML")
    if tuning_image.read_bytes() != tuning_overlay.read_bytes():
        fail("fresh rootfs HI1337 IPA tuning YAML differs from the source overlay")

    manifest = parse_manifest(manifest_path)
    zram_config = rootfs / "etc/systemd/zram-generator.conf"
    if not zram_config.is_file():
        fail("rootfs is missing the local zram-generator override in /etc")
    zram_text = zram_config.read_text(encoding="utf-8")
    if "[zram0]" not in zram_text or not re.search(r"^zram-size\s*=\s*4096\s*$", zram_text, re.MULTILINE):
        fail("rootfs zram-generator override must cap zram0 at 4096 MiB")
    port_path = rootfs / PORT_FILE.lstrip("/")
    document = json.loads(port_path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1,
        "port_id": "x810-fedora",
        "device_id": "SM-X810",
        "os_id": "fedora",
        "arch": "aarch64",
    }
    if not isinstance(document, dict) or any(document.get(k) != v for k, v in expected.items()):
        fail("rootfs port.json schema/target mismatch")
    if document.get("version") != version:
        fail("rootfs port.json version mismatch")

    manifest_fields = {
        "port_id": "x810-fedora",
        "port_version": version,
        "device_id": "SM-X810",
        "os_id": "fedora",
        "os_version": document.get("os_version"),
        "arch": "aarch64",
    }
    if any(manifest.get(key) != value for key, value in manifest_fields.items()):
        fail("rootfs manifest metadata does not match port.json")

    if version == "unknown":
        if rpm_path is not None:
            fail("unknown builds must not produce an updater-installable RPM")
        no_package = {
            "port_package_name": "none",
            "port_package_version": "unknown",
            "port_package_arch": "none",
            "port_package_asset": "none",
        }
        if any(manifest.get(key) != value for key, value in no_package.items()):
            fail("unknown build manifest must explicitly record no support RPM")
        installed = subprocess.run(
            ["rpm", "--root", str(rootfs), "-q", PACKAGE_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if installed.returncode == 0:
            fail("unknown image still contains the support RPM")
        return

    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        fail("expected numeric MAJOR.MINOR.PATCH port version")
    if rpm_path is None or not Path(rpm_path).is_file():
        fail("versioned release needs its support RPM")
    package_path = Path(rpm_path)
    package_version = rpm_query(package_path, "%{VERSION}-%{RELEASE}")
    package_arch = rpm_query(package_path, "%{ARCH}")
    package_name = rpm_query(package_path, "%{NAME}")
    if package_name != PACKAGE_NAME or package_arch != "aarch64" or not package_version.startswith(version + "-"):
        fail("support RPM name/version/architecture does not match release")
    package_fields = {
        "port_package_name": package_name,
        "port_package_version": package_version,
        "port_package_arch": package_arch,
        "port_package_asset": package_path.name,
    }
    if any(manifest.get(key) != value for key, value in package_fields.items()):
        fail("rootfs manifest support-RPM fields do not match the artifact")

    installed = subprocess.check_output(
        ["rpm", "--root", str(rootfs), "-q", "--qf", "%{NAME}\n%{VERSION}-%{RELEASE}\n%{ARCH}", PACKAGE_NAME],
        text=True,
    ).splitlines()
    if installed != [package_name, package_version, package_arch]:
        fail("installed rootfs support RPM does not match the published artifact")

    files = subprocess.check_output(["rpm", "-qpl", str(package_path)], text=True).splitlines()
    if PORT_FILE not in files:
        fail("support RPM does not own port.json")
    sensor_runtime_files = (
        "/usr/libexec/iio-sensor-proxy",
        "/usr/bin/monitor-sensor",
        "/usr/bin/ssccli",
        "/usr/lib64/libssc.so.2",
        "/usr/lib/systemd/system/iio-sensor-proxy.service",
        "/usr/lib/udev/rules.d/80-iio-sensor-proxy.rules",
        "/usr/share/dbus-1/system.d/net.hadess.SensorProxy.conf",
        "/usr/share/polkit-1/actions/net.hadess.SensorProxy.policy",
    )
    for sensor_file in sensor_runtime_files:
        if sensor_file not in files:
            fail(f"support RPM does not own the SSC sensor runtime file: {sensor_file}")
    if not any(re.match(r"^/usr/lib64/libssc\.so\.[0-9]", item) for item in files):
        fail("support RPM does not own the libssc runtime shared library")
    provides = subprocess.check_output(
        ["rpm", "-qp", "--provides", str(package_path)], text=True
    ).splitlines()
    if "iio-sensor-proxy = 3.9" not in provides:
        fail("support RPM does not provide iio-sensor-proxy = 3.9")
    if "libssc.so.2()(64bit)" not in provides:
        fail("support RPM does not provide libssc.so.2 runtime ABI")
    requires = subprocess.check_output(
        ["rpm", "-qp", "--requires", str(package_path)], text=True
    ).splitlines()
    if "libssc.so.2()(64bit)" not in requires:
        fail("support RPM does not declare its libssc.so.2 runtime requirement")
    for required_camera_file in (
        "/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so",
        HI1337_TUNING_FILE,
        "/etc/libcamera/configuration.yaml",
        "/etc/environment.d/90-x810-libcamera.conf",
    ):
        if required_camera_file not in files:
            fail(f"support RPM does not own its HI1337 libcamera integration: {required_camera_file}")
    if "/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so.sign" in files:
        fail("support RPM must not ship a signature that does not match Fedora's embedded IPA key")
    for vendor_config in ("/etc/locale.conf", "/etc/machine-info"):
        if vendor_config in files:
            fail(f"support RPM must not claim Fedora systemd config: {vendor_config}")
    if "/usr/lib/systemd/zram-generator.conf" in files:
        fail("support RPM must not replace Fedora's zram-generator-defaults file")
    if "/etc/systemd/zram-generator.conf" not in files:
        fail("support RPM does not own the /etc zram-generator override")
    if any(re.match(r"^/(?:boot(?:/|$)|lib/modules/|usr/lib/modules/|usr/lib/firmware/|lib/firmware/)", item) for item in files):
        fail("support RPM contains kernel, boot, or firmware payload")

    # Rootfs builds first copy the overlay unowned, then install this RPM to
    # make those exact existing files package-managed. Check that ownership
    # transfer succeeded for every packaged file (not just port.json).
    for sensor_file in sensor_runtime_files:
        image_file = rootfs / sensor_file.lstrip("/")
        if not image_file.is_file():
            fail(f"rootfs is missing the packaged sensor runtime file: {sensor_file}")
    for filename in files:
        image_file = rootfs / filename.lstrip("/")
        if not image_file.is_file():
            continue
        owner = subprocess.check_output(
            ["rpm", "--root", str(rootfs), "-q", "--qf", "%{NAME}", "-f", "/" + filename.lstrip("/")],
            text=True,
        ).strip()
        if owner != PACKAGE_NAME:
            fail(f"overlay file was not claimed by the support RPM: {filename}")

    # Prove the versioned port.json bytes are actually in the RPM payload by
    # comparing its payload digest with the exact image file.
    records = subprocess.check_output(
        ["rpm", "-qp", "--qf", "[%{FILENAMES}\\t%{FILEDIGESTS}\\n]", str(package_path)], text=True
    ).splitlines()
    payload_digests = {}
    for record in records:
        filename, separator, digest = record.partition("\t")
        if separator and filename in (PORT_FILE, HI1337_TUNING_FILE):
            payload_digests[filename] = digest
    for filename, image_path in (
        (PORT_FILE, port_path),
        (HI1337_TUNING_FILE, tuning_image),
    ):
        image_digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if payload_digests.get(filename) != image_digest:
            fail(f"support RPM {filename} payload differs from the fresh rootfs")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--rpm", help="required for numeric versions; forbidden for unknown")
    args = parser.parse_args(argv)
    try:
        check(args.rootfs, args.manifest, args.version, args.rpm)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as exc:
        print(f"test-port-build-contract: {exc}", file=sys.stderr)
        return 1
    print(f"Port build contract OK: version={args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
