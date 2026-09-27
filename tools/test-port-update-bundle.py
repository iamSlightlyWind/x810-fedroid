#!/usr/bin/env python3
"""Tests for the rolling X810 port-update bundle contract."""
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


writer = load("write_port_build_info", ROOT / "tools/write-port-build-info.py")
bundle = load("make_port_update_bundle", ROOT / "tools/make-port-update-bundle.py")


class PortUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rpm = self.root / "x810-fedora-port-0.1.0-1000000.42.fc44.aarch64.rpm"
        self.rpm.write_bytes(b"test rpm payload\n")
        self.info = self.root / "port-build.json"
        writer.write_build_info(self.info, version="0.1.0", run_id=9001, run_number=42,
                                commit="a" * 40, branch="main")

    def tearDown(self):
        self.temp.cleanup()

    def query_rpm(self, command, *, text):
        self.assertEqual(command[:4], ["rpm", "-qp", "--qf", "%{NAME}\n%{VERSION}\n%{RELEASE}\n%{ARCH}"])
        return "x810-fedora-port\n0.1.0\n1000000.42.fc44\naarch64"

    def test_emits_app_compatible_single_package_release_bundle(self):
        output = self.root / "x810-fedora-port.zip"
        with patch.object(bundle.subprocess, "check_output", side_effect=self.query_rpm):
            document = bundle.create_bundle(self.rpm, self.info, output)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["project"], "x810-fedora")
        self.assertEqual(document["run_id"], 9001)
        self.assertEqual(document["run_number"], 42)
        self.assertEqual(document["assets"][0]["target"], {
            "os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810",
        })
        self.assertEqual(document["assets"][0]["package_version"], "0.1.0-1000000.42.fc44")
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {"tab-companion-update.json", self.rpm.name})
            index = json.loads(archive.read("tab-companion-update.json"))
            self.assertEqual(index, document)

    def test_rejects_rpm_nevra_that_cannot_upgrade_per_build(self):
        with patch.object(bundle.subprocess, "check_output", return_value=
                          "x810-fedora-port\n0.1.0\n1.fc44\naarch64"):
            with self.assertRaisesRegex(ValueError, "does not uniquely increase"):
                bundle.create_bundle(self.rpm, self.info, self.root / "bad.zip")

    def test_rejects_wrong_rpm_target_or_port_version(self):
        with patch.object(bundle.subprocess, "check_output", return_value=
                          "x810-fedora-port\n0.1.1\n1000000.42.fc44\naarch64"):
            with self.assertRaisesRegex(ValueError, "does not match"):
                bundle.create_bundle(self.rpm, self.info, self.root / "bad.zip")
        with patch.object(bundle.subprocess, "check_output", return_value=
                          "x810-fedora-port\n0.1.0\n1000000.42.fc44\nnoarch"):
            with self.assertRaisesRegex(ValueError, "does not match"):
                bundle.create_bundle(self.rpm, self.info, self.root / "bad.zip")

    def test_build_info_rejects_bad_run_identity(self):
        with self.assertRaisesRegex(ValueError, "must be positive"):
            writer.write_build_info(self.info, version="0.1.0", run_id=0, run_number=0,
                                    commit="a" * 40, branch="main")


if __name__ == "__main__":
    unittest.main()
