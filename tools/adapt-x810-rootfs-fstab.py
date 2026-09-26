#!/usr/bin/env python3
"""Safely adapt an unpacked X710 Fedora rootfs fstab for X810 root-on-UFS.

Dry-run by default. With --apply, edits only the supplied host-side rootfs
directory, saving the original fstab next to it first. It does not access ADB,
block devices, or the tablet.
"""
from __future__ import annotations

import argparse
import difflib
from pathlib import Path

OLD_ROOT = "UUID=d2a235a8-37cd-4bac-be53-16caf2bfdd21"
OLD_BOOT = "UUID=b7869a36-d9a0-4403-b9fd-e0ebec016b76"
NEW_ROOT = "PARTLABEL=linuxroot"


def transform(src: str) -> str:
    lines = src.splitlines(keepends=True)
    out: list[str] = []
    found_root = found_boot = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        fields = stripped.split()
        if len(fields) < 4:
            raise ValueError(f"unrecognized fstab line; refusing: {stripped}")
        if fields[1] == "/":
            if fields[0] != OLD_ROOT or found_root:
                raise ValueError("unexpected/duplicate root fstab entry; refusing")
            found_root = True
            out.append(f"{NEW_ROOT} / ext4 defaults 0 0\n")
        elif fields[1] == "/boot":
            if fields[0] != OLD_BOOT or found_boot:
                raise ValueError("unexpected/duplicate /boot fstab entry; refusing")
            found_boot = True
            # Kernel/initramfs reside in Android boot-chain partitions.
            continue
        else:
            out.append(line)
    if not found_root or not found_boot:
        raise ValueError("did not find both expected X710 UUID entries; refusing")
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rootfs", type=Path, help="unpacked rootfs directory on host")
    ap.add_argument("--apply", action="store_true", help="write after making .x710-backup")
    args = ap.parse_args()
    fstab = args.rootfs / "etc/fstab"
    if not fstab.is_file():
        raise SystemExit(f"missing {fstab}")
    original = fstab.read_text()
    adapted = transform(original)
    print("".join(difflib.unified_diff(
        original.splitlines(keepends=True), adapted.splitlines(keepends=True),
        fromfile="etc/fstab (X710)", tofile="etc/fstab (X810)",
    )), end="")
    if args.apply:
        backup = fstab.with_name("fstab.x710-backup")
        if backup.exists():
            raise SystemExit(f"backup already exists: {backup}")
        backup.write_text(original)
        fstab.write_text(adapted)
        print(f"Applied host-side only; original saved as {backup}")
    else:
        print("Dry run only; pass --apply to edit the host-side rootfs.")


if __name__ == "__main__":
    main()
