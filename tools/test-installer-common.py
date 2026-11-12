#!/usr/bin/env python3
"""Contract tests for the byte-identical core shared by Fedora and Arch."""

import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
CORE_PATH = HERE / "x810_installer_common.py"
EXPECTED_CORE_SHA256 = "42905a3004dac05553f704d3aa327094443b12b045c3fdd4804bf2b5194d3239"
SPEC = importlib.util.spec_from_file_location("x810_installer_common_test", CORE_PATH)
assert SPEC is not None and SPEC.loader is not None
common = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = common
SPEC.loader.exec_module(common)


class SharedInstallerContractTests(unittest.TestCase):
    def test_core_matches_reviewed_shared_digest(self):
        self.assertEqual(hashlib.sha256(CORE_PATH.read_bytes()).hexdigest(), EXPECTED_CORE_SHA256)
        workspace = HERE.parent.parent
        for repository in ("x810-fedroid", "x810-btwdroid"):
            peer = workspace / repository / "tools" / "x810_installer_common.py"
            if peer.is_file() and peer.resolve() != CORE_PATH.resolve():
                self.assertEqual(CORE_PATH.read_bytes(), peer.read_bytes(), f"shared core drift: {peer}")

    def test_adb_parse_selects_exact_target_and_enforces_policy(self):
        listed = "List of devices attached\na\trecovery product:gts9pwifi\nb\tdevice product:gts9pwifi\n"
        self.assertEqual(common.adb_devices(listed), [("a", "recovery"), ("b", "device")])
        with self.assertRaisesRegex(RuntimeError, "multiple ADB targets"):
            common.adb_target("adb", None, lambda *_args, **_kwargs: type("R", (), {
                "returncode": 0, "stdout": listed, "stderr": "",
            })())
        result = common.adb_target("adb", "a", lambda *_args, **_kwargs: type("R", (), {
            "returncode": 0, "stdout": listed, "stderr": "",
        })())
        self.assertEqual(result, ("a", "recovery"))

    def test_sgdisk_parser_requires_complete_identity_and_extent(self):
        output = """Partition GUID code: 0FC63DAF-8483-4772-8E79-3D69D8477DE4
Partition unique GUID: 11111111-2222-3333-4444-555555555555
First sector: 6
Last sector: 264
Partition name: 'safe-prefix'
"""
        parsed = common.parse_sgdisk_info(output)
        self.assertEqual(parsed["first"], "6")
        self.assertEqual(parsed["last"], "264")
        self.assertIsNone(common.parse_sgdisk_info("Partition #36 does not exist.\n"))
        self.assertIsNone(common.parse_sgdisk_info("Partition name: 'incomplete'\n"))

    def test_device_profiles_share_security_checks_but_declare_evidence_policy(self):
        properties = {
            "ro.twrp.version": "3.7.1_12-0", "ro.product.device": "gts9pwifi",
            "ro.boot.em.model": "SM-X816B", "ro.bootloader": "X810XXS5CYG1",
            "ro.boot.flash.locked": "0", "ro.boot.vbmeta.device_state": "unlocked",
            "ro.boot.verifiedbootstate": "orange",
        }

        class Client:
            serial = "fixture"

            @staticmethod
            def shell(*args, **_kwargs):
                if args[:1] == ("getprop",):
                    value = properties.get(args[1], "")
                elif args == ("id", "-u"):
                    value = "0"
                elif args == ("cat", "/proc/cmdline"):
                    value = "androidboot.em.model=SM-X810"
                else:
                    value = ""
                return type("R", (), {"returncode": 0, "stdout": value + "\n"})()

        arch_values, arch_checks = common.twrp_identity(
            Client(), "recovery", expected_model="SM-X810",
            codenames={"gts9p", "gts9pwifi"}, bootloader_prefix="X810",
            model_aliases={"SM-X816B"}, require_bootloader=True,
        )
        self.assertEqual(arch_values["model"], "SM-X816B")
        self.assertFalse(any(check.state == "STOP" for check in arch_checks), arch_checks)

        fed_values, fed_checks = common.twrp_identity(
            Client(), "recovery", expected_model="SM-X810",
            codenames={"gts9p", "gts9pwifi"}, bootloader_prefix="X810",
            allow_cmdline_identity=True,
        )
        self.assertEqual(fed_values["model"], "SM-X816B")
        self.assertTrue(any(check.label == "Tablet identity" and check.state == "STOP" for check in fed_checks))


if __name__ == "__main__":
    unittest.main()
