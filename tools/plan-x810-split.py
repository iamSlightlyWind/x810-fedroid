#!/usr/bin/env python3
"""Read-only X810 userdata split calculator; accepts captured GPT edges only."""
from __future__ import annotations

import argparse
import struct
import uuid
import zlib
from pathlib import Path

BLOCK = 4096
WINDOW = 1 << 20
ENTRY = struct.Struct("<16s16sQQQ")
DISK_GUID = uuid.UUID("98101b32-bbe2-4bf2-a06e-2bb33d000c20")
USERDATA_GUID = uuid.UUID("1b81e7e6-f50d-419b-a739-2aeef8da3335")
LINUX_GUID = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")


def decode_capture(directory: Path) -> tuple[int, int, int, int]:
	first = (directory / "sda-first-1m.bin").read_bytes()
	last = (directory / "sda-last-1m.bin").read_bytes()
	if len(first) != WINDOW or len(last) != WINDOW:
		raise ValueError("need exact 1 MiB sda first/last captures")
	# Samsung X810 has a 4 KiB logical GPT sector; primary header is LBA 1.
	h = first[BLOCK:BLOCK + 92]
	if h[:8] != b"EFI PART":
		raise ValueError("missing primary GPT header")
	rev, hsize, hcrc, reserved, current, backup, usable_first, usable_last = struct.unpack_from("<IIIIQQQQ", h, 8)
	disk_guid = uuid.UUID(bytes_le=h[56:72])
	table_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", h, 72)
	if disk_guid != DISK_GUID or current != 1 or hsize != 92 or entry_size < 128:
		raise ValueError("unexpected X810 GPT identity/header geometry")
	check = bytearray(first[BLOCK:BLOCK + hsize]); struct.pack_into("<I", check, 16, 0)
	if zlib.crc32(check) & 0xffffffff != hcrc:
		raise ValueError("bad primary-header CRC")
	entries_off = table_lba * BLOCK
	arr = first[entries_off:entries_off + count * entry_size]
	if zlib.crc32(arr) & 0xffffffff != table_crc:
		raise ValueError("bad primary entry-array CRC")
	if len(arr) != count * entry_size or count < 35:
		raise ValueError("unexpected GPT entry array")
	ud = arr[33 * entry_size:34 * entry_size]
	type_bytes, guid_bytes, start, end, attrs = ENTRY.unpack_from(ud)
	name = ud[56:entry_size].decode("utf-16le").rstrip("\0")
	if name != "userdata" or uuid.UUID(bytes_le=type_bytes) != USERDATA_GUID:
		raise ValueError("GPT entry 34 is not expected X810 userdata")
	if uuid.UUID(bytes_le=guid_bytes) != USERDATA_GUID:
		raise ValueError("userdata GUID differs from the measured X810 value")
	for i in range(34, count):
		e = arr[i * entry_size:(i + 1) * entry_size]
		if e[:16] != bytes(16):
			pname = e[56:entry_size].decode("utf-16le").rstrip("\0")
			raise ValueError(f"GPT entry {i + 1} unexpectedly occupied: {pname}")
	# Verify backup header and array, not merely trusting the primary copy.
	total = backup + 1
	last_off = ((total - 1) - (total - WINDOW // BLOCK)) * BLOCK
	bh = last[last_off:last_off + 92]
	if bh[:8] != b"EFI PART": raise ValueError("missing backup GPT header")
	b_hcrc = struct.unpack_from("<I", bh, 16)[0]
	b_hsize = struct.unpack_from("<I", bh, 12)[0]
	bcheck = bytearray(bh[:b_hsize]); struct.pack_into("<I", bcheck, 16, 0)
	if zlib.crc32(bcheck) & 0xffffffff != b_hcrc:
		raise ValueError("bad backup-header CRC")
	b_table_lba, b_count, b_size, b_crc = struct.unpack_from("<QIII", bh, 72)
	b_array_off = (b_table_lba - (total - WINDOW // BLOCK)) * BLOCK
	barr = last[b_array_off:b_array_off + b_count * b_size]
	if b_count != count or b_size != entry_size or barr != arr or zlib.crc32(barr) & 0xffffffff != b_crc:
		raise ValueError("primary/backup GPT arrays mismatch")
	if not (start == 3_568_128 and end == usable_last == 62_275_574):
		raise ValueError("userdata geometry differs from audited CYG1 X810")
	return start, end, count, entry_size


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__)
	ap.add_argument("capture_dir", type=Path, nargs="?", default=Path("probes/android-baseline"))
	ap.add_argument("--android-percent", type=int, default=50)
	ap.add_argument("--align-sectors", type=int, default=512, help="4 KiB sectors; 512 = 2 MiB")
	a = ap.parse_args()
	if not 5 <= a.android_percent <= 95 or a.align_sectors <= 0:
		raise SystemExit("Android percentage must be 5..95 and alignment positive")
	start, end, count, _ = decode_capture(a.capture_dir)
	total = end - start + 1
	android = total * a.android_percent // 100
	android = android // a.align_sectors * a.align_sectors
	ud_end = start + android - 1
	lr_start = ud_end + 1
	linux = end - lr_start + 1
	if lr_start % a.align_sectors or linux <= 0:
		raise SystemExit("computed split fails alignment/bounds")
	fmt = lambda n: f"{n * BLOCK:,} B ({n * BLOCK / 2**30:.2f} GiB)"
	print(f"READ-ONLY X810 plan; validated GPT captures; entries={count}; 4K sectors")
	print(f"userdata: {start:,}..{ud_end:,} ({fmt(android)})")
	print(f"linuxroot: {lr_start:,}..{end:,} ({fmt(linux)}), type={LINUX_GUID}")
	print("No tablet or GPT was modified.")


if __name__ == "__main__":
	main()
