#!/usr/bin/env python3
"""Synthetic AOSP LP metadata tests for the X810 read-only vendor mapper."""

from __future__ import annotations

import hashlib
import os
import runpy
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-android-parts"
# Avoid writing Python bytecode into the production rootfs overlay.
dyn = SimpleNamespace(**runpy.run_path(str(SCRIPT)))


def lp_name(name: str, length: int = 36) -> bytes:
    raw = name.encode("ascii")
    if len(raw) >= length:
        raise ValueError(name)
    return raw + bytes(length - len(raw))


def make_super(
    path: Path,
    partitions: list[tuple[str, int, int, int, int]],
    extents: list[tuple[int, int, int, int]],
    *,
    size_bytes: int = 32 * 1024 * 1024,
    slot_count: int = 2,
    bad_primary: bool = False,
) -> None:
    """Write valid LP metadata directly, without touching a block device.

    Partition tuples are (name, attributes, first_extent, extent_count,
    group_index); extent tuples follow AOSP's <QIQI> structure.
    """
    max_meta = 64 * 1024
    first_sector = 2048
    part_rows = b"".join(
        lp_name(name) + struct.pack("<IIII", attrs, first, count, group)
        for name, attrs, first, count, group in partitions
    )
    extent_rows = b"".join(struct.pack("<QIQI", *row) for row in extents)
    group_rows = lp_name("default") + struct.pack("<IQ", 0, 0)
    block_rows = struct.pack(
        "<QIIQ36sI", first_sector, 4096, 0, size_bytes, lp_name("super"), 0
    )
    tables = part_rows + extent_rows + group_rows + block_rows
    pdesc = (0, len(partitions), 52)
    edesc = (len(part_rows), len(extents), 24)
    gdesc = (len(part_rows) + len(extent_rows), 1, 48)
    bdesc = (len(part_rows) + len(extent_rows) + len(group_rows), 1, 64)
    header_size = 128
    header = bytearray(
        struct.pack(
            "<IHHI32sI32s" + "III" * 4,
            dyn.HEADER_MAGIC,
            10,
            0,
            header_size,
            bytes(32),
            len(tables),
            hashlib.sha256(tables).digest(),
            *(pdesc + edesc + gdesc + bdesc),
        )
    )
    header[12:44] = hashlib.sha256(header).digest()
    geometry = bytearray(
        struct.pack("<II32sIII", dyn.GEOMETRY_MAGIC, 52, bytes(32), max_meta, slot_count, 4096)
    )
    geometry[8:40] = hashlib.sha256(geometry).digest()
    meta_copy = bytes(header) + tables
    with path.open("wb") as stream:
        stream.truncate(size_bytes)
    with path.open("r+b") as stream:
        stream.seek(4096)
        stream.write(geometry)
        stream.write(bytes(4096 - len(geometry)))
        stream.write(geometry)
        stream.write(bytes(4096 - len(geometry)))
        stream.seek(12288)
        if bad_primary:
            stream.write(bytes(max_meta))
        else:
            stream.write(meta_copy)
            stream.write(bytes(max_meta - len(meta_copy)))
        stream.seek(12288 + max_meta)
        stream.write(meta_copy)
        stream.write(bytes(max_meta - len(meta_copy)))


class DynamicPartitionTests(unittest.TestCase):
    def make_file(self, **kwargs) -> Path:
        handle = tempfile.NamedTemporaryFile(prefix="x810-super-", suffix=".img", delete=False)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        make_super(path, **kwargs)
        return path

    def test_maps_one_vendor_partition_read_only_table(self):
        image = self.make_file(
            partitions=[("vendor", 1, 0, 2, 0)],
            extents=[(8, 0, 2048, 0), (4, 1, 0, 0)],
        )
        metadata = dyn.read_metadata(str(image))
        vendor = dyn.choose_vendor(metadata)
        table = dyn.build_dm_table(metadata, vendor, str(image))
        self.assertEqual(
            table,
            f"0 8 linear {image} 2048\n8 4 zero",
        )
        self.assertEqual(vendor.name, "vendor")

    def test_uses_valid_backup_metadata_copy_if_primary_is_bad(self):
        image = self.make_file(
            partitions=[("vendor", 1, 0, 1, 0)],
            extents=[(8, 0, 2048, 0)],
            bad_primary=True,
        )
        metadata = dyn.read_metadata(str(image))
        self.assertEqual(dyn.choose_vendor(metadata).name, "vendor")

    def test_refuses_ambiguous_ab_vendor_without_suffix(self):
        image = self.make_file(
            partitions=[("vendor_a", 1, 0, 1, 0), ("vendor_b", 1, 1, 1, 0)],
            extents=[(8, 0, 2048, 0), (8, 0, 2056, 0)],
        )
        metadata = dyn.read_metadata(str(image))
        with self.assertRaisesRegex(dyn.MetadataError, "slot_suffix"):
            dyn.choose_vendor(metadata)
        self.assertEqual(dyn.choose_vendor(metadata, "_b").name, "vendor_b")

    def test_rejects_out_of_bounds_linear_extent(self):
        image = self.make_file(
            partitions=[("vendor", 1, 0, 1, 0)],
            extents=[(16, 0, (32 * 1024 * 1024) // 512 - 8, 0)],
        )
        with self.assertRaisesRegex(dyn.MetadataError, "escapes super"):
            dyn.read_metadata(str(image))

    def test_rejects_unexpected_super_size(self):
        image = self.make_file(
            partitions=[("vendor", 1, 0, 1, 0)],
            extents=[(8, 0, 2048, 0)],
            size_bytes=32 * 1024 * 1024,
        )
        with image.open("r+b") as stream:
            stream.truncate(31 * 1024 * 1024)
        metadata = dyn.read_metadata(str(image))
        with self.assertRaisesRegex(dyn.MetadataError, "size disagrees"):
            dyn.build_dm_table(metadata, dyn.choose_vendor(metadata), str(image))

    def test_rejects_corrupt_metadata_checksum(self):
        image = self.make_file(
            partitions=[("vendor", 1, 0, 1, 0)],
            extents=[(8, 0, 2048, 0)],
        )
        with image.open("r+b") as stream:
            stream.seek(12288 + 140)
            stream.write(b"X")
            stream.seek(12288 + 64 * 1024 + 140)
            stream.write(b"X")
        with self.assertRaises(dyn.MetadataError):
            dyn.read_metadata(str(image))

    def test_rejects_unsupported_physical_extent_source(self):
        image = self.make_file(
            partitions=[("vendor", 1, 0, 1, 0)],
            extents=[(8, 0, 2048, 2)],
        )
        with self.assertRaisesRegex(dyn.MetadataError, "source index"):
            dyn.read_metadata(str(image))

    def test_dry_run_never_invokes_device_mapper(self):
        image = self.make_file(
            partitions=[("vendor", 1, 0, 1, 0)],
            extents=[(8, 0, 2048, 0)],
        )
        table = dyn.map_vendor(str(image), "vendor-test-unit", dry_run=True)
        self.assertIn("linear", table)
        self.assertNotIn("dmsetup", table)

    def test_removes_new_mapping_if_readonly_verification_raises(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if args[0] == "blockdev":
                raise dyn.subprocess.CalledProcessError(1, args)
            return dyn.subprocess.CompletedProcess(args, 0, stdout="")

        with mock.patch.object(dyn.subprocess, "run", side_effect=run):
            with self.assertRaises(dyn.subprocess.CalledProcessError):
                dyn._create_readonly_mapping("vendor", "0 8 linear /dev/super 2048")

        self.assertIn(["dmsetup", "--readonly", "create", "vendor", "--table",
                       "0 8 linear /dev/super 2048"], calls)
        self.assertIn(["dmsetup", "remove", "vendor"], calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
