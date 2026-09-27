#!/usr/bin/env python3
"""Guard against claiming Fedora-owned machine configuration in the port RPM."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SupportRpmStagingTests(unittest.TestCase):
    def test_systemd_owned_configs_remain_image_local(self):
        builder = (ROOT / "tools/build-port-support-rpm.sh").read_text(encoding="utf-8")
        self.assertIn(
            'rm -f "$stage/etc/machine-info" "$stage/etc/locale.conf"', builder
        )
        self.assertFalse((ROOT / "rootfs/overlay/etc/locale.conf").exists())
        self.assertTrue((ROOT / "rootfs/overlay/etc/machine-info").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
