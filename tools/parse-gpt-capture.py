#!/usr/bin/env python3
"""Validate/print GPTs from read-only first/last 1 MiB block captures.

This program only reads host files. It does not access Android or block devices.
The current UFS captures use 4096-byte logical blocks and 256 blocks per file.
"""
from __future__ import annotations
import argparse
import struct
import uuid
import zlib
from pathlib import Path

BLOCK = 4096
WINDOW_BLOCKS = 256
HEADER = struct.Struct("<8sIIIIQQQQ16sQIII")
ENTRY = struct.Struct("<16s16sQQQ")


def parse_one(directory: Path, disk: str) -> str:
    first = (directory / f"{disk}-first-1m.bin").read_bytes()
    last = (directory / f"{disk}-last-1m.bin").read_bytes()
    if len(first) != BLOCK * WINDOW_BLOCKS or len(last) != len(first):
        raise ValueError(f"{disk}: expected two 1 MiB captures")

    def header_at(data: bytes, offset: int):
        h = data[offset:offset + HEADER.size]
        if len(h) != HEADER.size or h[:8] != b"EFI PART":
            raise ValueError(f"{disk}: missing GPT header at capture offset {offset}")
        fields = HEADER.unpack(h)
        _, revision, hsize, hcrc, reserved, current, backup, first_usable, last_usable, guid, table_lba, count, entry_size, table_crc = fields
        if not HEADER.size <= hsize <= BLOCK:
            raise ValueError(f"{disk}: unreasonable GPT header size {hsize}")
        check = bytearray(data[offset:offset + hsize])
        struct.pack_into("<I", check, 16, 0)
        if zlib.crc32(check) & 0xffffffff != hcrc:
            raise ValueError(f"{disk}: bad header CRC at {current}")
        return (revision, hsize, current, backup, first_usable, last_usable,
                uuid.UUID(bytes_le=guid), table_lba, count, entry_size, table_crc)

    # UFS GPT block 1 is at byte offset 4096 (LBA 1).
    ph = header_at(first, BLOCK)
    _, _, primary_lba, backup_lba, first_usable, last_usable, disk_guid, entries_lba, count, entry_size, entries_crc = ph
    total_blocks = backup_lba + 1
    if primary_lba != 1 or total_blocks <= WINDOW_BLOCKS:
        raise ValueError(f"{disk}: unexpected primary/backup geometry")
    primary_offset = entries_lba * BLOCK
    array_len = count * entry_size
    if entry_size < ENTRY.size or primary_offset + array_len > len(first):
        raise ValueError(f"{disk}: primary entry array is not in first capture")
    arr = first[primary_offset:primary_offset + array_len]
    if zlib.crc32(arr) & 0xffffffff != entries_crc:
        raise ValueError(f"{disk}: bad primary entry-array CRC")

    backup_offset = (total_blocks - WINDOW_BLOCKS) * BLOCK
    backup_header_offset = (total_blocks - 1 - (total_blocks - WINDOW_BLOCKS)) * BLOCK
    bh = header_at(last, backup_header_offset)
    _, _, current_b, backup_b, _, _, disk_guid_b, backup_entries_lba, count_b, size_b, crc_b = bh
    if current_b != backup_lba or backup_b != 1 or disk_guid_b != disk_guid:
        raise ValueError(f"{disk}: primary/backup headers disagree")
    array_offset = (backup_entries_lba - (total_blocks - WINDOW_BLOCKS)) * BLOCK
    barr = last[array_offset:array_offset + count_b * size_b]
    if count_b != count or size_b != entry_size or zlib.crc32(barr) & 0xffffffff != crc_b or barr != arr:
        raise ValueError(f"{disk}: backup entry array mismatch/CRC failure")

    lines = [f"{disk}: PASS header/array CRCs; {total_blocks:,} x {BLOCK}-byte LBAs; bytes={total_blocks*BLOCK}; disk_guid={disk_guid}; usable={first_usable}..{last_usable}"]
    for index in range(count):
        e = arr[index * entry_size:(index + 1) * entry_size]
        if not e or e[:16] == bytes(16):
            continue
        type_guid, part_guid, start, end, attrs = ENTRY.unpack_from(e)
        name = e[56:entry_size].decode("utf-16le", errors="strict").rstrip("\0")
        if end < start or start < first_usable or end > last_usable:
            raise ValueError(f"{disk} partition {index + 1}: invalid bounds")
        lines.append(f"  {index+1:2} {name:20} {start:>12}..{end:<12} bytes={(end-start+1)*BLOCK:<14} type={uuid.UUID(bytes_le=type_guid)} guid={uuid.UUID(bytes_le=part_guid)} attrs=0x{attrs:x}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture_dir", type=Path)
    ap.add_argument("disks", nargs="*", default=["sda", "sdb", "sdc", "sdd", "sde", "sdf"])
    args = ap.parse_args()
    for disk in args.disks:
        print(parse_one(args.capture_dir, disk))

if __name__ == "__main__":
    main()
