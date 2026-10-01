#!/usr/bin/env python3
"""Check that image and updater build one locked SSC sensor stack."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "specs/iio-sensor-proxy-libssc/sources.lock.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"test-x810-sensor-proxy-stack: {message}")


def main() -> int:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    require(lock.get("schema_version") == 1, "source lock schema changed")
    expected = {
        "libssc": ("0.4.4", "0cf77b93b55752da34dca2dcecc06fca8665184b",
                   "716d6bd6b34d2d753060c6b54c9a87e34fae75b724c763bf9ef487efa3621587"),
        "iio_sensor_proxy": ("3.9", "0085ddf8ecb173a1c5fcf2344aa40e561125354f",
                             "800682aa591fc672e959d2f3a43d1f4f7160a4c1cdabffd0ebff2bb8f3bb29be"),
    }
    for name, (version, commit, digest) in expected.items():
        entry = lock.get(name, {})
        require(entry.get("version") == version, f"{name} version is not pinned")
        require(entry.get("commit") == commit, f"{name} commit is not pinned")
        require(entry.get("sha256") == digest, f"{name} archive hash is not pinned")
        require(commit in entry.get("url", ""), f"{name} URL is not commit-addressed")

    helper = (ROOT / "tools/build-x810-sensor-proxy.sh").read_text(encoding="utf-8")
    rootfs_builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
    rpm_builder = (ROOT / "tools/build-port-support-rpm.sh").read_text(encoding="utf-8")
    spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/x810-fedora.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    issues = (ROOT / "docs/Known-Issues.md").read_text(encoding="utf-8")
    hardware_notes = (ROOT / "docs/Hardware-Notes.md").read_text(encoding="utf-8")
    sensor_proxy_unit = (ROOT / "rootfs/overlay/usr/lib/systemd/system/"
                         "gts9wifi-wait-sensor-proxy.service").read_text(encoding="utf-8")
    sensor_pd_dropin = (ROOT / "rootfs/overlay/etc/systemd/system/"
                        "hexagonrpcd-adsp-sensorspd.service.d/"
                        "10-gts9wifi-hexagonfs.conf").read_text(encoding="utf-8")

    # The X910 reference delays the sensor-PD claim until the desktop's first
    # SSC client exists; retain that ordering in both the image and updates.
    require("After=display-manager.service" in sensor_proxy_unit,
            "SSC recovery may run before GNOME opens its sensor client")
    require("WantedBy=graphical.target" in sensor_proxy_unit,
            "SSC recovery must be started by the desktop target, not multi-user")
    require("Motion sensors and SSC/SensorProxy are working" in readme and
            "GNOME currently lacks the auto-rotate option/integration" in readme,
            "README must distinguish working sensors from the missing GNOME integration")
    require("HasAccelerometer=true" in issues and
            "GNOME currently exposes no auto-rotate option" in issues,
            "known-issues page must record working SensorProxy and open GNOME integration")
    require("QRTR service 400 was present" in hardware_notes and
            "GNOME currently" in hardware_notes and "auto-rotate option/integration" in hardware_notes,
            "hardware notes must distinguish working SSC from missing GNOME integration")
    require("After=display-manager.service" in sensor_pd_dropin,
            "sensor-PD can still attach before GNOME and miss delayed SSC failure")
    require("systemctl reenable gts9wifi-wait-sensor-proxy.service" in spec,
            "RPM update does not migrate the old multi-user enablement link")
    require("systemctl try-restart gts9wifi-wait-sensor-proxy.service" not in spec,
            "RPM update must not start/restart the sensor recovery in a live session")

    for patch in (
        "notify-slow-sensor-discovery.patch",
        "sensor-proxy-early-claim-guard.patch",
        "start-polling-claimed-while-starting.patch",
    ):
        require((ROOT / "specs/iio-sensor-proxy-libssc/patches" / patch).is_file(),
                f"source patch is missing: {patch}")
    libssc_patches = ROOT / "specs/libssc-samsung/patches"
    for patch in (
        "fix-ssc-sync-wait-busy-loop.patch",
    ):
        require((libssc_patches / patch).is_file(),
                f"libssc source patch is missing: {patch}")
    require('specs/libssc-samsung/patches/*.patch' in helper,
            "shared builder does not apply the X810 libssc patch series")
    require("test-x810-libssc-patches.py" in helper,
            "shared builder does not test the patched libssc source")
    require('"$repo_dir"/specs/iio-sensor-proxy-libssc/patches/*.patch' in helper,
            "shared builder does not apply the locked patch series")
    require("test-x810-sensor-proxy-claim-race.py" in helper,
            "shared builder does not run the patched-source regression")
    require("-Dssc-support=enabled" in helper, "proxy build does not require SSC backend")
    require("meson test --no-rebuild --print-errorlogs" in helper,
            "shared builder does not run iio-sensor-proxy's hardware-independent tests")
    require("sha256sum" in helper and "sources.lock.json" in helper,
            "builder does not hash-check its source archives")
    require('bash "$repo_dir/tools/build-x810-sensor-proxy.sh" "$rootfs"' in rootfs_builder,
            "fresh rootfs does not call the shared sensor builder")
    require('bash "$repo_dir/tools/build-x810-sensor-proxy.sh" "$stage" >&2' in rpm_builder,
            "updater RPM does not call the shared sensor builder")
    require("source_libssc_commit=" + expected["libssc"][1] in rootfs_builder,
            "rootfs manifest source commit differs from lock")
    require("source_libssc_archive_sha256=" + expected["libssc"][2] in rootfs_builder,
            "rootfs manifest libssc archive checksum differs from lock")
    require("source_iio_sensor_proxy_commit=" + expected["iio_sensor_proxy"][1] in rootfs_builder,
            "rootfs manifest proxy commit differs from lock")
    require("source_iio_sensor_proxy_archive_sha256=" + expected["iio_sensor_proxy"][2] in rootfs_builder,
            "rootfs manifest proxy archive checksum differs from lock")
    require("Provides:       iio-sensor-proxy = 3.9" in spec,
            "support RPM does not replace the removed Fedora sensor-proxy capability")
    require("Requires:       libssc.so.2()(64bit)" in spec,
            "support RPM does not declare the bundled SSC runtime ABI")
    require("systemctl try-restart iio-sensor-proxy.service" in spec,
            "RPM does not refresh an already-running sensor proxy after update")
    require("systemctl restart hexagonrpcd-adsp-sensorspd.service" not in spec,
            "RPM must not restart ADSP/sensorspd during update")

    required_deps = (
        "libgudev-devel", "libqmi-devel", "protobuf-c-devel", "qrtr-devel",
        "xz-devel", "python3-devel", "python3-protobuf",
    )
    build_update = workflow.split("  build_port_update:", 1)[1].split("\n  plan:", 1)[0]
    for package in required_deps:
        require(re.search(rf"\b{re.escape(package)}\b", build_update) is not None,
                f"ARM support-RPM container omits build dependency {package}")
    require("fedora44-toolchain-v3" in build_update,
            "support-RPM DNF cache key was not bumped for the expanded toolchain")
    require("python3 tools/test-x810-sensor-proxy-stack.py" in build_update,
            "support-RPM job does not run sensor stack contract tests")
    require("python3 tools/test-x810-dmic-ucm.py" in workflow,
            "existing DMIC regression invocation was removed")

    print("PASS: rootfs and updater use the same pinned SSC sensor builder and RPM update contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
