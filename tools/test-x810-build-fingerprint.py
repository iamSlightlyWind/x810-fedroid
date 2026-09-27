#!/usr/bin/env python3
"""Tests for stable and component-scoped X810 build identities."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from importlib.util import module_from_spec, spec_from_file_location


SCRIPT = Path(__file__).with_name("x810-build-fingerprint.py")
SPEC = spec_from_file_location("x810_build_fingerprint", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        for rel, content in (
            ("kernel/kernel.spec", "kernel one\n"),
            ("boot/cmdline.txt", "console=tty0\n"),
            ("tools/make-twrp-zip.py", "zip one\n"),
            ("rootfs/build-rootfs.sh", "rootfs one\n"),
            ("rootfs/overlay/etc/issue", "fedora\n"),
            ("specs/x810-fedora-port.spec", "port one\n"),
            ("tools/build-port-support-rpm.sh", "rpm one\n"),
            ("tools/stamp-port-metadata.py", "stamp one\n"),
            ("tools/test-port-build-contract.py", "contract one\n"),
            ("tools/verify-x810-rootfs-archive.py", "archive one\n"),
            ("tools/make-port-release-index.py", "index one\n"),
            ("tools/build-x810-clean-install-bundle.py", "bundle one\n"),
            ("tools/verify-x810-build-match.py", "match one\n"),
            ("tools/x810-build-fingerprint.py", "fingerprint one\n"),
            ("tools/publish-x810-full-set.sh", "full set one\n"),
            ("tools/bdftool.py", "bdf one\n"),
        ):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, component, **overrides):
        args = {
            "root": self.root,
            "ref": "HEAD",
            "firmware_sha256": "f" * 64,
            "fedora_base_compose": "Fedora-44-20260422.1",
            "fedora_updates_compose": "Fedora-44-updates-20260926.0",
            "fedora_release": "44",
            "desktop": "gnome",
            "port_version": "0.1.0",
            "kernel_rpm_sha256": "a" * 64,
        }
        args.update(overrides)
        return MODULE.fingerprint(component, **args)

    def commit_change(self, rel):
        subprocess.run(["git", "-C", str(self.root), "add", rel], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "change"], check=True)

    def test_same_source_inputs_produce_same_key(self):
        self.assertEqual(self.key("kernel"), self.key("kernel"))

    def test_rootfs_changes_do_not_invalidate_kernel(self):
        kernel_before = self.key("kernel")
        rootfs_before = self.key("rootfs")
        (self.root / "rootfs/build-rootfs.sh").write_text("rootfs two\n", encoding="utf-8")
        self.commit_change("rootfs/build-rootfs.sh")
        self.assertEqual(kernel_before, self.key("kernel"))
        self.assertNotEqual(rootfs_before, self.key("rootfs"))

    def test_kernel_changes_invalidate_both_kernel_and_dependent_rootfs(self):
        kernel_before = self.key("kernel")
        rootfs_before = self.key("rootfs")
        (self.root / "kernel/kernel.spec").write_text("kernel two\n", encoding="utf-8")
        self.commit_change("kernel/kernel.spec")
        self.assertNotEqual(kernel_before, self.key("kernel"))
        self.assertNotEqual(rootfs_before, self.key("rootfs", kernel_rpm_sha256="b" * 64))

    def test_component_parameters_are_in_the_key(self):
        base = self.key("rootfs")
        self.assertNotEqual(base, self.key("rootfs", desktop="core"))
        self.assertNotEqual(base, self.key("rootfs", port_version="0.2.0"))
        self.assertNotEqual(base, self.key("rootfs", kernel_rpm_sha256="b" * 64))
        self.assertNotEqual(base, self.key("rootfs", fedora_updates_compose="Fedora-44-updates-next"))

    def test_recipe_revision_changes_only_its_component_key(self):
        kernel_before = self.key("kernel")
        rootfs_before = self.key("rootfs")
        self.assertNotEqual(
            kernel_before,
            self.key("kernel", kernel_recipe_revision="2"),
        )
        self.assertNotEqual(
            rootfs_before,
            self.key("rootfs", rootfs_recipe_revision="2"),
        )
        self.assertEqual(
            kernel_before,
            self.key("kernel", rootfs_recipe_revision="2"),
        )
        self.assertEqual(
            rootfs_before,
            self.key("rootfs", kernel_recipe_revision="2"),
        )

    def test_full_set_key_tracks_assembler_inputs_and_both_components(self):
        common = {
            "kernel_build_key": "k" * 64,
            "rootfs_build_key": "r" * 64,
            "port_version": "0.1.0",
        }
        base = self.key("full-set", **common)
        kernel_before = self.key("kernel")
        rootfs_before = self.key("rootfs")
        self.assertNotEqual(base, self.key("full-set", **{**common, "kernel_build_key": "x" * 64}))
        self.assertNotEqual(base, self.key("full-set", **{**common, "rootfs_build_key": "x" * 64}))
        self.assertNotEqual(base, self.key("full-set", **{**common, "full_set_recipe_revision": "2"}))
        (self.root / "tools/make-port-release-index.py").write_text("index two\n", encoding="utf-8")
        self.commit_change("tools/make-port-release-index.py")
        after_index_change = self.key("full-set", **common)
        (self.root / "tools/publish-x810-full-set.sh").write_text("full set two\n", encoding="utf-8")
        self.commit_change("tools/publish-x810-full-set.sh")
        self.assertNotEqual(after_index_change, self.key("full-set", **common))
        self.assertNotEqual(base, self.key("full-set", **common))
        self.assertEqual(kernel_before, self.key("kernel"))
        self.assertEqual(rootfs_before, self.key("rootfs"))

    def test_fingerprint_can_be_recomputed_at_an_older_commit(self):
        old = self.key("kernel")
        ref = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        (self.root / "kernel/kernel.spec").write_text("kernel two\n", encoding="utf-8")
        self.commit_change("kernel/kernel.spec")
        self.assertEqual(old, self.key("kernel", ref=ref))
        self.assertNotEqual(old, self.key("kernel"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
