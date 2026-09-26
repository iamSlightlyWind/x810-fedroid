#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validate", ROOT / "tools/validate-x810-cmdline.py")
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)


class X810CmdlineTest(unittest.TestCase):
    def test_committed_cmdline_matches_live_verified_x810(self):
        self.assertEqual(validate.validate((ROOT / "boot/cmdline.txt").read_text()), [])

    def test_rejects_old_x710_panel_and_root_uuid(self):
        bad = "root=UUID=deadbeef msm_drm.dsi_display0=GTS9_ANA38407_AMSA10FA01: msm_drm.lcd_id=800004 sec_common_fn.lcd_id=800004"
        errors = validate.validate(bad)
        self.assertTrue(any("root=" in error for error in errors))
        self.assertTrue(any("msm_drm.dsi_display0" in error for error in errors))
        self.assertTrue(any("forbidden" in error for error in errors))

    def test_rejects_duplicate_roots(self):
        good = (ROOT / "boot/cmdline.txt").read_text().strip()
        errors = validate.validate(good + " root=PARTLABEL=userdata")
        self.assertTrue(any("expected exactly one root=" in error for error in errors))

    def test_bundle_builder_checks_cmdline_before_image_tools(self):
        build = (ROOT / "boot/build-bundle.sh").read_text()
        check = 'validate-x810-cmdline.py" "$cmdline_file"'
        self.assertIn(check, build)
        self.assertLess(build.index(check), build.index('command -v lz4'))


if __name__ == "__main__":
    unittest.main()
