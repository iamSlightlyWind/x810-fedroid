#!/usr/bin/env python3
"""Tests for rootfs port.json stamping (no Fedora container required)."""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = Path(__file__).with_name("stamp-port-metadata.py")
SPEC = importlib.util.spec_from_file_location("stamp_port_metadata", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PortMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rootfs = Path(self.temp.name)
        target = self.rootfs / module.PORT_PATH
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "rootfs/overlay" / module.PORT_PATH, target)
        self.path = target

    def tearDown(self):
        self.temp.cleanup()

    def test_default_remains_unknown_and_records_built_fedora_release(self):
        result = module.stamp_port_metadata(self.rootfs, "unknown", "44")
        self.assertEqual(result["version"], "unknown")
        self.assertEqual(result["os_version"], "44")
        self.assertEqual(result["port_id"], "x810-fedora")
        self.assertEqual(result["workflow_file"], "x810-fedora.yml")
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["artifact_name"], "update")
        self.assertIs(result["public_release"], True)
        self.assertEqual(result["release_tag_prefix"], "x810-fedora-port-build")
        self.assertEqual(result["build_info_path"], "/usr/share/tab-companion/port-build.json")
        self.assertEqual(json.loads(self.path.read_text()), result)

    def test_explicit_numeric_version_is_stamped(self):
        result = module.stamp_port_metadata(self.rootfs, "0.1.0", "rawhide")
        self.assertEqual(result["version"], "0.1.0")
        self.assertEqual(result["os_version"], "rawhide")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)

    def test_build_tags_cannot_be_guessed_or_stamped_as_port_versions(self):
        for invalid in ("", "rootfs-f44-gnome-5", "v1.2.3", "01.2.3", "1.2", "1.2.3-rc1"):
            with self.subTest(version=invalid), self.assertRaises(ValueError):
                module.stamp_port_metadata(self.rootfs, invalid, "44")

    def test_invalid_os_version_and_wrong_port_template_fail_closed(self):
        with self.assertRaises(ValueError):
            module.stamp_port_metadata(self.rootfs, "unknown", "44/evil")
        template = json.loads(self.path.read_text())
        template["device_id"] = "SM-X910"
        self.path.write_text(json.dumps(template))
        with self.assertRaises(ValueError):
            module.stamp_port_metadata(self.rootfs, "0.1.0", "44")


if __name__ == "__main__":
    unittest.main()
