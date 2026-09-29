#!/usr/bin/env python3
"""Check safe EFS handling and systemd ordering for X810 BT address setup."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-bt-address"
UNIT = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-bt-address.service"
PRESET = ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/85-gts9wifi.preset"
BUILDER = ROOT / "rootfs/build-rootfs.sh"


def main() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    unit = UNIT.read_text(encoding="utf-8")
    preset = PRESET.read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")

    assert 'mount -o ro,noload "$efs" "$mountpoint"' in script
    assert 'umount "$mountpoint"' in script
    assert "btmgmt_() { printf '' | timeout 8 btmgmt \"$@\"" in script
    assert 'btmgmt_ --index 0 public-addr "$address"' in script
    assert "controller is not in the configurable list" in script
    assert "After=bluetooth.service" in unit
    assert "Wants=bluetooth.service" in unit
    assert "TimeoutStartSec=90" in unit
    assert "enable gts9wifi-bt-address.service" in preset
    assert "gts9wifi-bt-address bluetooth" in builder
    # This unit may read the EFS address but must never write it or patch boot
    # partitions. The separate provisioner owns those image writes.
    assert "r+b" not in script and "dd if=" not in script
    print("PASS: bounded runtime Bluetooth address setup reads EFS ro,noload after BlueZ")


if __name__ == "__main__":
    main()
