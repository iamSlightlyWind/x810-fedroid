#!/usr/bin/env python3
"""Check X810 zram policy is a local override, not a Fedora-owned file."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "rootfs/overlay"


class RootfsZramConfigTests(unittest.TestCase):
    def test_zram_cap_uses_etc_override_and_does_not_replace_vendor_file(self):
        override = OVERLAY / "etc/systemd/zram-generator.conf"
        vendor_file = OVERLAY / "usr/lib/systemd/zram-generator.conf"
        self.assertTrue(override.is_file(), "missing /etc zram-generator override")
        self.assertFalse(vendor_file.exists(), "overlay must not overwrite Fedora vendor config")
        contents = override.read_text(encoding="utf-8")
        self.assertIn("[zram0]", contents)
        self.assertRegex(contents, re.compile(r"^zram-size\s*=\s*4096\s*$", re.MULTILINE))
        self.assertIn("conf.d drop-in", contents)
        app_override = OVERLAY / "etc/systemd/zram-generator.conf.d/90-tab-companion.conf"
        self.assertFalse(app_override.exists(), "rootfs must not bake a per-user Tab Companion choice")


if __name__ == "__main__":
    unittest.main(verbosity=2)
