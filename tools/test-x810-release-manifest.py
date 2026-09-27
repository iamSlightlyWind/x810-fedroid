#!/usr/bin/env python3
"""Contract tests for the compact X810 aggregate release metadata."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import tempfile
import unittest
import json
from argparse import Namespace
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("x810_release_manifest", TOOLS / "x810-release-manifest.py")
manifest_tool = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(manifest_tool)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checksum(directory: Path, filenames: list[str]) -> str:
    return "".join(f"{digest(directory / name)}  {name}\n" for name in filenames)


class ReleaseManifestTest(unittest.TestCase):
    def make_component_dirs(self, root: Path) -> tuple[Path, Path]:
        kernel = root / "kernel"
        rootfs = root / "rootfs"
        kernel.mkdir()
        rootfs.mkdir()
        (kernel / "boot.img").write_bytes(b"boot")
        (kernel / "init_boot.img").write_bytes(b"init")
        (kernel / "vendor_boot.img").write_bytes(b"vendor")
        (kernel / "dtbo.img").write_bytes(b"dtbo")
        (kernel / "linux-x810-1.aarch64.rpm").write_bytes(b"kernel rpm")
        (kernel / "x810-fedora-bootset-7.2.0-gts9wifi.zip").write_bytes(b"boot set zip")
        (kernel / "KERNEL-BUILD-KEY.txt").write_text("kernel-key\n", encoding="ascii")
        (kernel / "BUILD-METADATA.txt").write_text("repository_commit=" + "a" * 40 + "\n", encoding="utf-8")
        (kernel / "BUNDLE-SHA256SUMS").write_text(
            checksum(kernel, ["boot.img", "init_boot.img", "vendor_boot.img", "dtbo.img", "KERNEL-BUILD-KEY.txt"]),
            encoding="ascii",
        )
        (kernel / "RPM-SHA256SUMS").write_text(checksum(kernel, ["linux-x810-1.aarch64.rpm"]), encoding="ascii")

        (rootfs / "rootfs.tar.gz").write_bytes(b"rootfs")
        (rootfs / "port.rpm").write_bytes(b"support rpm")
        (rootfs / "ROOTFS-BUILD-KEY.txt").write_text("rootfs-key\n", encoding="ascii")
        (rootfs / "rootfs-manifest.txt").write_text("device_id=SM-X810\n", encoding="utf-8")
        names = ["rootfs.tar.gz", "port.rpm", "ROOTFS-BUILD-KEY.txt", "rootfs-manifest.txt"]
        (rootfs / "SHA256SUMS").write_text(checksum(rootfs, names), encoding="ascii")
        return kernel, rootfs

    def test_create_and_materialize_combines_public_metadata_and_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kernel, rootfs = self.make_component_dirs(root)
            payload = root / "x810-fedora-port.zip"
            payload.write_bytes(b"support update")
            assets = [*kernel.glob("*.img"), *kernel.glob("*.rpm"), *kernel.glob("*.zip"),
                      rootfs / "rootfs.tar.gz", rootfs / "port.rpm", payload]
            args = Namespace(
                source_commit="a" * 40,
                kernel_dir=kernel,
                rootfs_dir=rootfs,
                kernel_release="kernel-tag",
                rootfs_release="rootfs-tag",
                release_tag="aggregate-tag",
                port_version="1.2.3",
                full_set_build_key="full-key",
                asset=[str(item) for item in assets],
            )
            document = manifest_tool.create(args)
            self.assertEqual(document["source_commit"], "a" * 40)
            self.assertEqual(document["build_keys"], {
                "kernel": "kernel-key", "rootfs": "rootfs-key", "full_set": "full-key"
            })
            self.assertIn(payload.name, {entry["name"] for entry in document["assets"]})

            kernel_target = root / "materialized-kernel"
            rootfs_target = root / "materialized-rootfs"
            kernel_target.mkdir()
            rootfs_target.mkdir()
            for source in kernel.iterdir():
                if source.name not in {"BUILD-METADATA.txt", "BUNDLE-SHA256SUMS", "RPM-SHA256SUMS", "KERNEL-BUILD-KEY.txt"}:
                    shutil.copy2(source, kernel_target / source.name)
            for source in rootfs.iterdir():
                if source.name not in {"rootfs-manifest.txt", "SHA256SUMS", "ROOTFS-BUILD-KEY.txt"}:
                    shutil.copy2(source, rootfs_target / source.name)
            manifest_path = root / "release.json"
            manifest_path.write_text(__import__("json").dumps(document), encoding="utf-8")
            manifest_tool.materialize(manifest_path, "kernel", kernel_target)
            manifest_tool.materialize(manifest_path, "rootfs", rootfs_target)
            self.assertEqual((kernel_target / "KERNEL-BUILD-KEY.txt").read_text(), "kernel-key\n")
            self.assertEqual((rootfs_target / "rootfs-manifest.txt").read_text(), "device_id=SM-X810\n")

            rpm_only_target = root / "rpm-only"
            rpm_only_target.mkdir()
            shutil.copy2(next(kernel.glob("*.rpm")), rpm_only_target)
            manifest_tool.materialize(manifest_path, "kernel", rpm_only_target, rpm_only=True)
            self.assertTrue((rpm_only_target / "RPM-SHA256SUMS").is_file())

    def test_materialize_rejects_changed_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kernel, rootfs = self.make_component_dirs(root)
            payload = root / "payload"
            payload.write_bytes(b"x")
            document = manifest_tool.create(Namespace(
                source_commit="b" * 40, kernel_dir=kernel, rootfs_dir=rootfs,
                kernel_release="k", rootfs_release="r", release_tag="aggregate",
                port_version="1.2.3", full_set_build_key="f",
                asset=[str(item) for item in [*kernel.glob("*.img"), *kernel.glob("*.rpm"), *kernel.glob("*.zip"),
                                               rootfs / "rootfs.tar.gz", rootfs / "port.rpm", payload]],
            ))
            target = root / "tampered"
            shutil.copytree(kernel, target)
            (target / "boot.img").write_bytes(b"tampered")
            path = root / "manifest.json"
            import json
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                manifest_tool.materialize(path, "kernel", target)

    def test_standalone_support_rpm_is_omitted_from_aggregate_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kernel, rootfs = self.make_component_dirs(root)
            old = rootfs / "port.rpm"
            rpm = rootfs / "x810-fedora-port-1.2.3-1000000.1.fc44.noarch.rpm"
            old.rename(rpm)
            names = ["rootfs.tar.gz", rpm.name, "ROOTFS-BUILD-KEY.txt", "rootfs-manifest.txt"]
            (rootfs / "SHA256SUMS").write_text(checksum(rootfs, names), encoding="ascii")
            updater = root / "x810-fedora-port.zip"
            updater.write_bytes(b"updater zip placeholder")
            assets = [*kernel.glob("*.img"), kernel / "linux-x810-1.aarch64.rpm",
                      rootfs / "rootfs.tar.gz", updater]
            document = manifest_tool.create(Namespace(
                source_commit="c" * 40, kernel_dir=kernel, rootfs_dir=rootfs,
                kernel_release="kernel-tag", rootfs_release="rootfs-tag",
                release_tag="aggregate-tag", port_version="1.2.3", full_set_build_key="full-key",
                asset=[str(item) for item in assets],
            ))
            self.assertNotIn(rpm.name, {item["name"] for item in document["assets"]})
            self.assertEqual([item["name"] for item in document["components"]["rootfs"]["asset_files"]],
                             ["rootfs.tar.gz"])
            target = root / "materialized-rootfs"
            target.mkdir()
            shutil.copy2(rootfs / "rootfs.tar.gz", target)
            release_manifest = root / "manifest.json"
            release_manifest.write_text(json.dumps(document), encoding="utf-8")
            manifest_tool.materialize(release_manifest, "rootfs", target)
            self.assertNotIn(rpm.name, (target / "SHA256SUMS").read_text())

    def test_public_asset_names_are_short_and_materializable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kernel, rootfs = self.make_component_dirs(root)
            support_rpm = rootfs / "x810-fedora-port-1.2.3-1000000.5.fc44.noarch.rpm"
            (rootfs / "port.rpm").rename(support_rpm)
            rootfs_names = ["rootfs.tar.gz", support_rpm.name, "ROOTFS-BUILD-KEY.txt", "rootfs-manifest.txt"]
            (rootfs / "SHA256SUMS").write_text(checksum(rootfs, rootfs_names), encoding="ascii")
            kernel_rpm = root / "kernel.rpm"
            rootfs_tar = root / "rootfs.tar.gz"
            shutil.copy2(kernel / "linux-x810-1.aarch64.rpm", kernel_rpm)
            shutil.copy2(rootfs / "rootfs.tar.gz", rootfs_tar)
            update = root / "update.zip"
            update.write_bytes(b"updater zip placeholder")
            assets = [*kernel.glob("*.img"), kernel_rpm, rootfs_tar, update]
            document = manifest_tool.create(Namespace(
                source_commit="e" * 40, kernel_dir=kernel, rootfs_dir=rootfs,
                kernel_release="kernel-tag", rootfs_release="rootfs-tag",
                release_tag="aggregate-tag", port_version="1.2.3", full_set_build_key="full-key",
                asset=[str(item) for item in assets],
            ))
            self.assertEqual({item["name"] for item in document["assets"]},
                             {"boot.img", "init_boot.img", "vendor_boot.img", "dtbo.img",
                              "kernel.rpm", "rootfs.tar.gz", "update.zip"})
            self.assertIn("kernel.rpm", document["components"]["kernel"]["checksums"]["RPM-SHA256SUMS"])
            self.assertIn("rootfs.tar.gz", document["components"]["rootfs"]["checksums"]["SHA256SUMS"])
            kernel_target, rootfs_target = root / "materialized-kernel", root / "materialized-rootfs"
            kernel_target.mkdir()
            rootfs_target.mkdir()
            for image in kernel.glob("*.img"):
                shutil.copy2(image, kernel_target / image.name)
            shutil.copy2(kernel_rpm, kernel_target / "kernel.rpm")
            shutil.copy2(rootfs_tar, rootfs_target / "rootfs.tar.gz")
            release_manifest = root / "manifest.json"
            release_manifest.write_text(json.dumps(document), encoding="utf-8")
            manifest_tool.materialize(release_manifest, "kernel", kernel_target)
            manifest_tool.materialize(release_manifest, "rootfs", rootfs_target)
            manifest_tool.verify_component(kernel_target, document["components"]["kernel"], "kernel")
            manifest_tool.verify_component(rootfs_target, document["components"]["rootfs"], "rootfs")

    def test_old_manifest_can_be_materialized_without_retired_bootset_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kernel, rootfs = self.make_component_dirs(root)
            payload = root / "payload"
            payload.write_bytes(b"aggregate support")
            assets = [*kernel.glob("*.img"), kernel / "linux-x810-1.aarch64.rpm",
                      kernel / "x810-fedora-bootset-7.2.0-gts9wifi.zip",
                      rootfs / "rootfs.tar.gz", rootfs / "port.rpm", payload]
            document = manifest_tool.create(Namespace(
                source_commit="d" * 40, kernel_dir=kernel, rootfs_dir=rootfs,
                kernel_release="kernel-tag", rootfs_release="rootfs-tag",
                release_tag="aggregate-tag", port_version="1.2.3", full_set_build_key="full-key",
                asset=[str(item) for item in assets],
            ))
            legacy_zip = next(item for item in document["assets"] if item["name"].startswith("x810-fedora-bootset-"))
            document["components"]["kernel"]["asset_files"].append(legacy_zip)
            target = root / "kernel-target"
            target.mkdir()
            for source in kernel.iterdir():
                if source.name not in {"BUILD-METADATA.txt", "BUNDLE-SHA256SUMS", "RPM-SHA256SUMS", "KERNEL-BUILD-KEY.txt",
                                       "x810-fedora-bootset-7.2.0-gts9wifi.zip"}:
                    shutil.copy2(source, target / source.name)
            manifest_path = root / "legacy.json"
            manifest_path.write_text(json.dumps(document), encoding="utf-8")
            manifest_tool.materialize(manifest_path, "kernel", target)
            self.assertTrue((target / "BUNDLE-SHA256SUMS").is_file())


if __name__ == "__main__":
    unittest.main()
