#!/usr/bin/env python3
"""Guard the X810 no-suspend mitigation in both image and RPM paths."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "rootfs/overlay"
LOGIND = OVERLAY / "etc/systemd/logind.conf.d/10-gts9wifi-lid.conf"
ROOTFS_BUILDER = ROOT / "rootfs/build-rootfs.sh"
RPM_BUILDER = ROOT / "tools/build-port-support-rpm.sh"
MASKED_TARGETS = (
    "sleep.target",
    "suspend.target",
    "hibernate.target",
    "hybrid-sleep.target",
    "suspend-then-hibernate.target",
)


def main() -> None:
    settings = {}
    for line in LOGIND.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip()] = value.strip()

    for key in (
        "HandleLidSwitch",
        "HandleLidSwitchExternalPower",
        "HandleLidSwitchDocked",
    ):
        if settings.get(key) != "ignore":
            raise AssertionError(f"{key} must stay ignored until X810 resume is fixed")

    for target in MASKED_TARGETS:
        mask = OVERLAY / "etc/systemd/system" / target
        if not mask.is_symlink() or mask.readlink() != Path("/dev/null"):
            raise AssertionError(f"systemd sleep target is not masked: {mask}")

    # The overlay is the single source for clean images and support-RPM
    # updates, so this mitigation cannot be image-only or update-only.
    for builder in (ROOTFS_BUILDER, RPM_BUILDER):
        if 'cp -a "$repo_dir/rootfs/overlay/.' in builder.read_text(encoding="utf-8"):
            continue
        raise AssertionError(f"build path does not copy the rootfs overlay: {builder}")

    print("PASS: lid-close suspend is disabled until X810 resume is validated")
    print("PASS: all standard systemd sleep targets are masked")
    print("PASS: clean-image and support-RPM builders both consume the same overlay")
    print("NOTE: this is a safety mitigation; it does not repair suspend/wake")


if __name__ == "__main__":
    main()
