#!/usr/bin/env python3
"""Contract-check bounded, non-interactive btmgmt use in the manual BT reset."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-bt-revive"


def main() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert "btmgmt_() {" in source
    assert "printf '' | timeout 5 btmgmt \"$@\"" in source
    assert "btmgmt_ info | grep -q '^hci0:'" in source
    assert "btmgmt_ config | grep -q '^hci0:'" in source
    assert "btmgmt info 2>/dev/null" not in source
    assert "btmgmt config 2>/dev/null" not in source
    print("PASS: Bluetooth recovery polls BlueZ non-interactively with bounded calls")


if __name__ == "__main__":
    main()
