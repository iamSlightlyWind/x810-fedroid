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
PACKAGE_NAME = "x810-fedora-port"


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
    if package_name != PACKAGE_NAME or package_arch != "noarch" or not package_version.startswith(version + "-"):
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
    payload_digest = None
    for record in records:
        filename, separator, digest = record.partition("\t")
        if separator and filename == PORT_FILE:
            payload_digest = digest
            break
    image_digest = hashlib.sha256(port_path.read_bytes()).hexdigest()
    if payload_digest != image_digest:
        fail("support RPM port.json payload differs from the rootfs metadata")


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
