#!/usr/bin/env python3
"""Unit checks for the port-release index/runtime contract."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("make-port-release-index.py")
SPEC = importlib.util.spec_from_file_location("port_release_index", SCRIPT)
indexer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(indexer)


class ReleaseIndexTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.version = "0.1.2"
        self.package_version = "0.1.2-1.fc44"
        self.asset_name = "x810-fedora-port-0.1.2-1.fc44.noarch.rpm"
        self.rpm = self.root / self.asset_name
        self.rpm.write_bytes(b"test rpm content\n")
        self.manifest = self.root / "rootfs-manifest.txt"
        self.port_json = self.root / "port.json"
        self.port_json.write_text(
            json.dumps({
                "schema_version": 1,
                "port_id": "x810-fedora",
                "name": "Fedora on Samsung Galaxy Tab S9+ Wi-Fi",
                "repo_url": "https://github.com/iamSlightlyWind/x810-fedroid",
                "version": self.version,
                "device_id": "SM-X810",
                "os_id": "fedora",
                "os_version": "44",
                "arch": "aarch64",
            }),
            encoding="utf-8",
        )
        self.write_manifest()

    def tearDown(self):
        self.tempdir.cleanup()

    def write_manifest(self, **overrides):
        fields = {
            "port_id": "x810-fedora",
            "port_version": self.version,
            "device_id": "SM-X810",
            "os_id": "fedora",
            "os_version": "44",
            "arch": "aarch64",
            "port_package_name": "x810-fedora-port",
            "port_package_version": self.package_version,
            "port_package_arch": "noarch",
            "port_package_asset": self.asset_name,
        }
        fields.update(overrides)
        self.manifest.write_text(
            "".join(f"{key}={value}\n" for key, value in fields.items()), encoding="utf-8"
        )

    def rpm_query(self, command, *, text):
        self.assertEqual(command[:4], ["rpm", "-qp", "--qf", "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}"])
        self.assertEqual(command[4], str(self.rpm))
        return f"x810-fedora-port\n{self.package_version}\nnoarch"

    def test_emits_exact_app_compatible_one_asset_index(self):
        with patch.object(indexer.subprocess, "check_output", side_effect=self.rpm_query):
            document = indexer.create_index(
                self.manifest, self.rpm, self.port_json, "iamSlightlyWind/x810-fedroid", "x810-full-2026.1"
            )

        self.assertEqual(set(document), {"schema_version", "project", "version", "notes", "assets"})
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["project"], "x810-fedora")
        self.assertEqual(document["version"], self.version)
        self.assertEqual(len(document["assets"]), 1)
        asset = document["assets"][0]
        self.assertEqual(
            set(asset),
            {"name", "url", "sha256", "size", "format", "package_name", "package_version", "target"},
        )
        self.assertEqual(asset["name"], self.asset_name)
        self.assertEqual(
            asset["url"],
            "https://github.com/iamSlightlyWind/x810-fedroid/releases/download/x810-full-2026.1/"
            + self.asset_name,
        )
        self.assertEqual(asset["sha256"], indexer.sha256(self.rpm))
        self.assertEqual(asset["size"], self.rpm.stat().st_size)
        self.assertEqual(asset["format"], "rpm")
        self.assertEqual(asset["package_name"], "x810-fedora-port")
        self.assertEqual(asset["package_version"], self.package_version)
        target = {"os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810"}
        self.assertEqual(asset["target"], target)
        self.assertIs(indexer.validate_runtime_index(document, target=target), asset)

    def test_refuses_unknown_port_version(self):
        self.write_manifest(port_version="unknown")
        with patch.object(indexer.subprocess, "check_output", side_effect=self.rpm_query):
            with self.assertRaisesRegex(ValueError, "explicit numeric"):
                indexer.create_index(
                    self.manifest, self.rpm, self.port_json, "iamSlightlyWind/x810-fedroid", "release"
                )

    def test_refuses_unsafe_tag_before_url_construction(self):
        for tag in ("../bad", "tag?x=1", "tag#fragment", "tag with space"):
            with self.subTest(tag=tag):
                with patch.object(indexer.subprocess, "check_output", side_effect=self.rpm_query):
                    with self.assertRaisesRegex(ValueError, "tag contains unsupported characters"):
                        indexer.create_index(self.manifest, self.rpm, self.port_json, "iamSlightlyWind/x810-fedroid", tag)

    def test_refuses_mismatched_rpm_nevra(self):
        with patch.object(indexer.subprocess, "check_output", return_value="x810-fedora-port\n0.1.1-1.fc44\nnoarch"):
            with self.assertRaisesRegex(ValueError, "does not match"):
                indexer.create_index(self.manifest, self.rpm, self.port_json, "iamSlightlyWind/x810-fedroid", "release")

    def test_refuses_wrong_device_target(self):
        with patch.object(indexer.subprocess, "check_output", side_effect=self.rpm_query):
            document = indexer.create_index(self.manifest, self.rpm, self.port_json, "iamSlightlyWind/x810-fedroid", "release")
        document["assets"][0]["target"]["device"] = "SM-X716B"
        with self.assertRaisesRegex(ValueError, "exact target"):
            indexer.validate_runtime_index(
                document,
                target={"os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810"},
            )

    def test_refuses_repo_url_that_does_not_match_publisher_repository(self):
        document = json.loads(self.port_json.read_text(encoding="utf-8"))
        document["repo_url"] = "https://github.com/someone-else/x810-fedroid"
        self.port_json.write_text(json.dumps(document), encoding="utf-8")
        with patch.object(indexer.subprocess, "check_output", side_effect=self.rpm_query):
            with self.assertRaisesRegex(ValueError, "canonical repository URL"):
                indexer.create_index(self.manifest, self.rpm, self.port_json, "iamSlightlyWind/x810-fedroid", "release")


if __name__ == "__main__":
    unittest.main()
