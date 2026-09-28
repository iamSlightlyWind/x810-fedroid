#!/usr/bin/env python3
"""Unit tests for the X810 CYG1 Adreno firmware staging/checker."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("x810_gpu_firmware", ROOT / "tools/x810-gpu-firmware.py")
assert SPEC and SPEC.loader
firmware = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = firmware
SPEC.loader.exec_module(firmware)


def cpio_newc_file(name: str, content: bytes, mode: int = 0o100644) -> bytes:
    encoded_name = name.encode() + b"\0"
    values = (1, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(encoded_name), 0)
    entry = b"070701" + b"".join(f"{value:08x}".encode() for value in values)
    entry += encoded_name
    entry += b"\0" * ((-len(entry)) & 3)
    entry += content
    entry += b"\0" * ((-len(entry)) & 3)
    return entry


def cpio_trailer() -> bytes:
    return cpio_newc_file("TRAILER!!!", b"")


class FirmwareStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payloads = {"a740_zap.mdt": b"signed-zap-metadata", "a740_sqe.fw": b"sqe-firmware"}
        self.expected = {name: hashlib.sha256(data).hexdigest() for name, data in self.payloads.items()}

    def tearDown(self):
        self.tmp.cleanup()

    def test_stages_only_verified_blobs(self):
        source = self.root / "source"
        source.mkdir()
        for name, data in self.payloads.items():
            (source / name).write_bytes(data)
        destination = self.root / "dest"
        firmware.stage(source, destination, self.expected)
        self.assertEqual({p.name: p.read_bytes() for p in destination.iterdir()}, self.payloads)

    def test_wrong_firmware_is_rejected(self):
        source = self.root / "source"
        source.mkdir()
        (source / "a740_zap.mdt").write_bytes(b"generic-not-samsung-signed")
        (source / "a740_sqe.fw").write_bytes(self.payloads["a740_sqe.fw"])
        with self.assertRaisesRegex(ValueError, "wrong X810 CYG1 firmware"):
            firmware.read_firmware_set(source, self.expected)

    def test_tar_source_is_read_without_extracting_paths(self):
        archive_path = self.root / "fw.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, data in self.payloads.items():
                import io
                info = tarfile.TarInfo(f"usr/lib/firmware/qcom/{name}")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            info = tarfile.TarInfo("../../escape")
            info.size = 1
            import io
            archive.addfile(info, io.BytesIO(b"x"))
        self.assertEqual(firmware.read_firmware_set(archive_path, self.expected), self.payloads)

    def test_newc_parser_finds_qcom_firmware_files(self):
        content = self.payloads["a740_zap.mdt"]
        archive = cpio_newc_file("usr/lib/firmware/qcom/a740_zap.mdt", content) + cpio_trailer()
        self.assertEqual(firmware._parse_newc(archive)["a740_zap.mdt"], content)

    def test_vendor_boot_checker_requires_exact_firmware(self):
        initramfs = b"".join(
            cpio_newc_file(f"usr/lib/firmware/qcom/{name}", content)
            for name, content in self.payloads.items()
        ) + cpio_trailer()
        image = bytearray(4096 + len(initramfs))
        image[:8] = b"VNDRBOOT"
        struct.pack_into("<II", image, 8, 4, 4096)
        struct.pack_into("<I", image, 24, len(initramfs))
        image[4096:] = initramfs
        image_path = self.root / "vendor_boot.img"
        image_path.write_bytes(image)
        fake_lz4 = self.root / "lz4-test-double"
        fake_lz4.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "sys.stdout.buffer.write(pathlib.Path(sys.argv[-1]).read_bytes())\n",
            encoding="utf-8",
        )
        fake_lz4.chmod(0o755)
        firmware.check_vendor_boot(image_path, str(fake_lz4), self.expected)
        wrong = dict(self.expected)
        wrong["a740_zap.mdt"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "generic/wrong GPU firmware"):
            firmware.check_vendor_boot(image_path, str(fake_lz4), wrong)


if __name__ == "__main__":
    unittest.main()
