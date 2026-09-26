#!/usr/bin/env python3
"""Fail closed unless the boot cmdline matches the live-verified SM-X810 setup."""
from pathlib import Path
import argparse
import sys

EXPECTED = {
    "root": "PARTLABEL=linuxroot",
    "rootfstype": "ext4",
    "msm_drm.dsi_display0": "GTS9P_ANA38407_AMSA24VU05:",
    "msm_drm.lcd_id": "800005",
    "sec_common_fn.lcd_id": "800005",
}
FORBIDDEN = ("root=UUID=", "GTS9_ANA38407_AMSA10FA01", "lcd_id=800004")


def validate(text: str) -> list[str]:
    words = text.split()
    errors = []
    for key, value in EXPECTED.items():
        found = [word.split("=", 1)[1] for word in words if word.startswith(key + "=")]
        if found != [value]:
            errors.append(f"expected exactly one {key}={value}, got {found!r}")
    for marker in FORBIDDEN:
        if marker in text:
            errors.append(f"forbidden stale X710/UUID value: {marker}")
    if len(text.strip().encode()) >= 2048:
        errors.append("cmdline exceeds Android vendor cmdline capacity (2048 bytes)")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        text = args.file.read_text()
    except OSError as exc:
        print(f"cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    errors = validate(text)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: {args.file} matches live-verified SM-X810 root and panel parameters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
