#!/usr/bin/env python3
"""Host-only tests for exact rootfs/kernel release input matching."""

import hashlib
import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("verify-x810-build-match.py")
LOADER = importlib.machinery.SourceFileLoader("verify_x810_build_match", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class BuildMatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rpm = self.root / "linux-gts9wifi-test.rpm"
        self.rpm.write_bytes(b"matched kernel RPM bytes")
        self.rpm_sha = hashlib.sha256(self.rpm.read_bytes()).hexdigest()
        self.firmware_sha = hashlib.sha256(b"matched firmware payload").hexdigest()
        self.nevra = "linux-gts9wifi-7.2.0-0.1.fc44.aarch64"
        self.root_manifest = self.root / "rootfs-manifest.txt"
        self.kernel_metadata = self.root / "BUILD-METADATA.txt"
        self._write()

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, root_rpm_sha=None, kernel_rpm_sha=None,
               root_firmware_sha=None, kernel_firmware_sha=None,
               root_nevra=None, kernel_nevra=None):
        self.root_manifest.write_text(
            f"kernel_rpm_nevra={root_nevra or self.nevra}\n"
            f"kernel_rpm_sha256={root_rpm_sha or self.rpm_sha}\n"
            f"firmware_sha256={root_firmware_sha or self.firmware_sha}\n",
            encoding="utf-8",
        )
        self.kernel_metadata.write_text(
            f"kernel_rpm_nevra={kernel_nevra or self.nevra}\n"
            f"kernel_rpm_sha256={kernel_rpm_sha or self.rpm_sha}\n"
            f"firmware_sha256={kernel_firmware_sha or self.firmware_sha}\n",
            encoding="utf-8",
        )

    def test_matching_artifacts_pass(self):
        module.verify(self.root_manifest, self.kernel_metadata, self.rpm)

    def test_same_nevra_but_different_kernel_bytes_fails(self):
        other_sha = hashlib.sha256(b"different package same NEVRA").hexdigest()
        self._write(root_rpm_sha=other_sha)
        with self.assertRaisesRegex(ValueError, "exact same kernel RPM bytes"):
            module.verify(self.root_manifest, self.kernel_metadata, self.rpm)

    def test_firmware_digest_mismatch_fails(self):
        other_sha = hashlib.sha256(b"different firmware").hexdigest()
        self._write(root_firmware_sha=other_sha)
        with self.assertRaisesRegex(ValueError, "same firmware archive"):
            module.verify(self.root_manifest, self.kernel_metadata, self.rpm)

    def test_nevra_mismatch_fails(self):
        self._write(root_nevra="other-7.2.0-0.1.fc44.aarch64")
        with self.assertRaisesRegex(ValueError, "NEVRA"):
            module.verify(self.root_manifest, self.kernel_metadata, self.rpm)

    def test_missing_digest_fails_closed(self):
        self._write(root_rpm_sha="unknown")
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            module.verify(self.root_manifest, self.kernel_metadata, self.rpm)


if __name__ == "__main__":
    unittest.main()
