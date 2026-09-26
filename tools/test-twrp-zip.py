#!/usr/bin/env python3
"""Host-only contract tests for the SM-X810 TWRP boot-set ZIP."""

import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
BUILDER = PROJECT / "tools/make-twrp-zip.py"
UPDATER = PROJECT / "tools/twrp/mainline-update-binary"
SIZES = {
    "boot.img": 100_663_296,
    "init_boot.img": 8_388_608,
    "vendor_boot.img": 100_663_296,
    "dtbo.img": 16_777_216,
}


class TwrpZipTests(unittest.TestCase):
    def test_build_contract_contains_only_x810_boot_set_and_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "bundle"
            bundle.mkdir()
            for name, size in SIZES.items():
                with (bundle / name).open("wb") as stream:
                    stream.truncate(size)
            output = root / "x810.zip"
            subprocess.run(
                [sys.executable, str(BUILDER), str(bundle), str(output), "--project", str(PROJECT)],
                check=True,
                capture_output=True,
                text=True,
            )
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    set(SIZES) | {
                        "META-INF/com/google/android/update-binary",
                        "META-INF/com/google/android/updater-script",
                        "BUNDLE-LABEL",
                        "SHA256SUMS",
                    },
                )
                sums = archive.read("SHA256SUMS").decode()
                records = dict(line.split("  ", 1)[::-1] for line in sums.splitlines())
                for name, size in SIZES.items():
                    self.assertEqual(len(archive.read(name)), size)
                    self.assertEqual(records[name], hashlib.sha256(bytes(size)).hexdigest())
                updater = archive.read("META-INF/com/google/android/update-binary").decode()
                self.assertIn("SM-X810", updater)
                self.assertNotIn("vbmeta.img", updater)
                self.assertNotIn("userdata", updater)

    def test_updater_is_x810_only_and_never_writes_vbmeta_or_old_sd_overlay(self):
        source = UPDATER.read_text(encoding="utf-8")
        self.assertIn("gts9pwifi", source)
        self.assertIn("X810", source)
        self.assertIn("read-back verification", source)
        self.assertNotIn("SM-X710", source)
        self.assertNotIn("vbmeta.img", source)
        self.assertNotIn("of=.*vbmeta", source)
        subprocess.run(["sh", "-n", str(UPDATER)], check=True)


if __name__ == "__main__":
    unittest.main()
