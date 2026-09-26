#!/usr/bin/env python3
"""Host-only tests for the X810 single-boot TWRP ZIP contract."""

import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
BUILDER = PROJECT / "tools/make-singleboot-twrp-zip.py"
UPDATER = PROJECT / "tools/twrp/x810-boot-only-update-binary"
BOOT_SIZE = 100_663_296
EXPECTED_FILES = {
    "boot.img",
    "META-INF/com/google/android/update-binary",
    "META-INF/com/google/android/updater-script",
    "BUNDLE-LABEL",
    "SHA256SUMS",
}


class SingleBootZipTests(unittest.TestCase):
    def test_zip_contains_only_boot_image_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "boot.img"
            with image.open("wb") as stream:
                stream.truncate(BOOT_SIZE)
            output = root / "x810-boot.zip"
            subprocess.run(
                [sys.executable, str(BUILDER), str(image), str(output), "--project", str(PROJECT)],
                check=True,
                capture_output=True,
                text=True,
            )
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), EXPECTED_FILES)
                data = archive.read("boot.img")
                self.assertEqual(len(data), BOOT_SIZE)
                sums = archive.read("SHA256SUMS").decode().splitlines()
                self.assertEqual(sums, [f"{hashlib.sha256(data).hexdigest()}  boot.img"])
                updater = archive.read("META-INF/com/google/android/update-binary").decode()
                self.assertIn("SM-X810", updater)
                self.assertIn("/dev/block/by-name/boot", updater)
                self.assertIn("read-back", updater)
                self.assertNotIn("vbmeta.img", updater)
                self.assertNotIn("vendor_boot.img", updater)
                self.assertNotIn("dtbo.img", updater)
                self.assertNotIn("init_boot.img", updater)

    def test_installer_shell_syntax_and_boot_only_write_target(self):
        source = UPDATER.read_text(encoding="utf-8")
        self.assertIn("blockdev --getsize64", source)
        self.assertIn("blockdev --getro", source)
        self.assertIn("dd if=\"$TMP/boot.img\" of=\"$TARGET\"", source)
        self.assertNotIn("of=/dev/block/by-name/vbmeta", source)
        self.assertNotIn("of=/dev/block/by-name/vendor_boot", source)
        self.assertNotIn("of=/dev/block/by-name/init_boot", source)
        self.assertNotIn("of=/dev/block/by-name/dtbo", source)
        subprocess.run(["sh", "-n", str(UPDATER)], check=True)

    def test_builder_rejects_wrong_partition_sized_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "boot.img"
            image.write_bytes(b"not a partition image")
            result = subprocess.run(
                [sys.executable, str(BUILDER), str(image), str(root / "bad.zip"), "--project", str(PROJECT)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected 100663296", result.stderr)


if __name__ == "__main__":
    unittest.main()
