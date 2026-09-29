#!/usr/bin/env python3
"""Tests exact CYG1 VPU firmware staging for the early-boot initramfs."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("x810_vpu_firmware", ROOT / "tools/x810-vpu-firmware.py")
assert SPEC and SPEC.loader
firmware = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = firmware
SPEC.loader.exec_module(firmware)


class VpuFirmwareTests(unittest.TestCase):
    def test_tracked_blob_hash_matches_expected(self):
        blob = ROOT / "firmware/x810-vpu-cyg1/vpu30_4v.mbn"
        self.assertEqual(hashlib.sha256(blob.read_bytes()).hexdigest(), firmware.EXPECTED_SHA256)

    def test_stage_copies_exact_named_blob_and_rejects_modified_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "vpu30_4v.mbn"
            source.write_bytes((ROOT / "firmware/x810-vpu-cyg1/vpu30_4v.mbn").read_bytes())
            dest = root / "usr/lib/firmware/qcom/vpu"
            firmware.stage(source, dest)
            staged = dest / "vpu30_4v.mbn"
            self.assertTrue(staged.is_file())
            self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), firmware.EXPECTED_SHA256)
            source.write_bytes(b"not the CYG1 PAS image")
            with self.assertRaisesRegex(ValueError, "exact SM-X810 CYG1 SHA-256"):
                firmware.stage(source, dest)

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.mbn"
            real.write_bytes(b"dummy")
            alias = root / "vpu30_4v.mbn"
            alias.symlink_to(real)
            with self.assertRaisesRegex(ValueError, "regular non-symlink"):
                firmware.stage(alias, root / "dest")


if __name__ == "__main__":
    unittest.main()
