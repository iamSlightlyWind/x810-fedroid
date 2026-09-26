#!/usr/bin/env python3
"""Host-only tests for the no-GPT X810 root filesystem resize service."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-grow-rootfs"


class RootfsGrowthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        self._fake("findmnt", """#!/bin/sh
case "$*" in
  "-no SOURCE /") echo "${MOCK_SOURCE:-/dev/mock-linuxroot}" ;;
  "-no FSTYPE /") echo "${MOCK_FSTYPE:-ext4}" ;;
  *) exit 2 ;;
esac
""")
        self._fake("lsblk", """#!/bin/sh
[ "$*" = "-nro PARTLABEL /dev/mock-linuxroot" ] || exit 2
echo "${MOCK_PARTLABEL:-linuxroot}"
""")
        self._fake("resize2fs", """#!/bin/sh
echo "$*" >> "$MOCK_CALLS"
""")
        self._fake("df", """#!/bin/sh
echo "filesystem size used avail use% mounted on"
echo "mock 1 1 0 100% /"
""")
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GTS9_ROOTFS_GROWN_STAMP": str(self.root / "state/grown"),
            "MOCK_CALLS": str(self.calls),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _fake(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_service(self, **overrides):
        env = self.env | overrides
        return subprocess.run(["sh", str(SCRIPT)], env=env, text=True,
                              capture_output=True, check=False)

    def test_resizes_only_linuxroot_ext4_and_stamps_success(self):
        result = self.run_service()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().strip(), "/dev/mock-linuxroot")
        self.assertTrue((self.root / "state/grown").exists())

    def test_completed_service_is_idempotent(self):
        stamp = self.root / "state/grown"
        stamp.parent.mkdir()
        stamp.touch()
        result = self.run_service()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_other_partition_label_is_not_resized(self):
        result = self.run_service(MOCK_PARTLABEL="userdata")
        self.assertEqual(result.returncode, 0)
        self.assertIn("not linuxroot", result.stdout)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.root / "state/grown").exists())

    def test_non_ext4_root_is_not_resized(self):
        result = self.run_service(MOCK_FSTYPE="f2fs")
        self.assertEqual(result.returncode, 0)
        self.assertIn("not ext4", result.stdout)
        self.assertFalse(self.calls.exists())

    def test_service_contains_no_partition_table_or_format_operations(self):
        source = SCRIPT.read_text()
        for dangerous in ("sfdisk", "partx", "partprobe", "sgdisk", "parted", "wipefs", "mkfs"):
            self.assertNotIn(dangerous, source)
        self.assertIn('resize2fs "$root_src"', source)
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)


if __name__ == "__main__":
    unittest.main()
