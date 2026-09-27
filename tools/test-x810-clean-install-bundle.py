#!/usr/bin/env python3
"""Tests for deterministic and fail-closed X810 clean-install bundle assembly."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location(
    "build_x810_clean_install_bundle", TOOLS / "build-x810-clean-install-bundle.py"
)
builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(builder)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checksum_file(directory: Path, names: list[str], target: str) -> None:
    lines = [f"{digest((directory / name).read_bytes())}  {name}" for name in names]
    (directory / target).write_text("\n".join(lines) + "\n", encoding="ascii")


def make_tar(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


class CleanInstallBundleTest(unittest.TestCase):
    def make_inputs(self, base: Path) -> tuple[Path, Path, int]:
        kernel_dir = base / "kernel"
        rootfs_dir = base / "rootfs"
        kernel_dir.mkdir()
        rootfs_dir.mkdir()

        rpm_data = b"test kernel rpm payload\n"
        rpm_name = "linux-x810-7.2.0-1.aarch64.rpm"
        (kernel_dir / rpm_name).write_bytes(rpm_data)
        rpm_sha = digest(rpm_data)
        firmware_sha = "a" * 64
        nevra = "linux-x810-7.2.0-1.aarch64"
        metadata = (
            f"kernel_rpm_nevra={nevra}\n"
            f"kernel_rpm_sha256={rpm_sha}\n"
            f"firmware_sha256={firmware_sha}\n"
            "kernel_source=fixture\n"
        )
        (kernel_dir / "BUILD-METADATA.txt").write_text(metadata, encoding="utf-8")
        (kernel_dir / "KERNEL-BUILD-KEY.txt").write_text("kernel-test-key\n", encoding="ascii")
        image_sizes = {"boot": 1, "init_boot": 2, "vendor_boot": 3, "dtbo": 4}
        for name, size in image_sizes.items():
            (kernel_dir / f"{name}.img").write_bytes(bytes([size]) * size)
        checksum_file(kernel_dir, ["boot.img", "init_boot.img", "vendor_boot.img", "dtbo.img"], "BUNDLE-SHA256SUMS")
        checksum_file(kernel_dir, [rpm_name], "RPM-SHA256SUMS")

        port = {
            "schema_version": 1,
            "port_id": "x810-fedora",
            "device_id": "SM-X810",
            "os_id": "fedora",
            "os_version": "44",
            "arch": "aarch64",
            "version": "1.2.3",
        }
        payloads = {
            "etc/fstab": b"PARTLABEL=linuxroot / ext4 defaults 0 1\n",
            "etc/os-release": b"NAME=Fedora\nID=fedora\nVERSION_ID=44\n",
            "etc/passwd": b"root:x:0:0:root:/root:/bin/bash\n",
            "etc/shadow": b"root:!::0:99999:7:::\n",
            "etc/hostname": b"localhost.localdomain\n",
            "usr/share/tab-companion/port.json": json.dumps(port).encode(),
            "usr/lib/modules/7.2.0-gts9wifi/kernel/test.ko": b"module fixture",
            "usr/share/test-root-file": b"x" * 177,
        }
        archive_path = rootfs_dir / "x810-fedora-44-rootfs.tar.gz"
        make_tar(archive_path, payloads)
        expanded_bytes = sum(map(len, payloads.values()))
        root_manifest = (
            "port_id=x810-fedora\n"
            "port_version=1.2.3\n"
            "device_id=SM-X810\n"
            "os_id=fedora\n"
            "os_version=44\n"
            "arch=aarch64\n"
            f"kernel_rpm_nevra={nevra}\n"
            f"kernel_rpm_sha256={rpm_sha}\n"
            f"firmware_sha256={firmware_sha}\n"
        )
        (rootfs_dir / "rootfs-manifest.txt").write_text(root_manifest, encoding="utf-8")
        (rootfs_dir / "ROOTFS-BUILD-KEY.txt").write_text("rootfs-test-key\n", encoding="ascii")
        checksum_file(
            rootfs_dir,
            [archive_path.name, "rootfs-manifest.txt"],
            "SHA256SUMS",
        )
        return kernel_dir, rootfs_dir, expanded_bytes

    def test_bundle_manifest_inventory_and_archive_are_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            kernel_dir, rootfs_dir, content_bytes = self.make_inputs(base)
            image_sizes = {"boot": 1, "init_boot": 2, "vendor_boot": 3, "dtbo": 4}
            common = dict(
                bundle_version="x810-fedora-7.2.0-1",
                kernel_release="kernel-7.2.0-gts9wifi-1",
                rootfs_release="rootfs-f44-gnome-5",
                source_commit="a" * 40,
                full_set_build_key="full-set-test-key",
                image_sizes=image_sizes,
                minimum_rootfs_floor=1,
                rootfs_headroom_floor=0,
            )
            first = base / "one.tar.gz"
            second = base / "two.tar.gz"
            manifest = builder.build_bundle(kernel_dir, rootfs_dir, first, **common)
            builder.build_bundle(kernel_dir, rootfs_dir, second, **common)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["type"], "x810-clean-install")
            self.assertEqual(manifest["device"], {"model": "SM-X810", "codename": "gts9pwifi"})
            self.assertEqual(manifest["os"], {"id": "fedora", "version": "44", "arch": "aarch64"})
            self.assertEqual(manifest["source_commit"], "a" * 40)
            self.assertEqual(manifest["build_keys"], {
                "kernel": "kernel-test-key", "rootfs": "rootfs-test-key", "full_set": "full-set-test-key"
            })
            self.assertEqual(manifest["kernel"]["release"], "7.2.0-gts9wifi")
            self.assertEqual(manifest["rootfs"]["minimum_size_bytes"], content_bytes + (content_bytes * 20 + 99) // 100)
            self.assertEqual(set(manifest["boot_set"]["images"]), set(image_sizes))

            with tarfile.open(first, "r:gz") as archive:
                names = set(archive.getnames())
                self.assertIn("x810-clean-install-manifest.json", names)
                self.assertIn("rootfs/rootfs-manifest.txt", names)
                self.assertIn("kernel/BUILD-METADATA.txt", names)
                self.assertIn("metadata/SOURCE-RELEASES.txt", names)
                self.assertFalse(any("vbmeta" in name or "recovery.img" in name for name in names))
                packaged_manifest = json.load(archive.extractfile("x810-clean-install-manifest.json"))
                self.assertEqual(packaged_manifest, manifest)
                for name, item in manifest["files"].items():
                    data = archive.extractfile(name).read()
                    self.assertEqual(len(data), item["size_bytes"])
                    self.assertEqual(digest(data), item["sha256"])

    def test_default_minimum_has_32_gib_floor_and_expansion_margin(self) -> None:
        gib = 1024**3
        self.assertEqual(builder.minimum_rootfs_size(0), 32 * gib)
        self.assertEqual(builder.minimum_rootfs_size(30 * gib), 36 * gib)

    def test_modified_image_is_rejected_against_release_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            kernel_dir, rootfs_dir, _ = self.make_inputs(base)
            (kernel_dir / "boot.img").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                builder.build_bundle(
                    kernel_dir,
                    rootfs_dir,
                    base / "bad.tar.gz",
                    bundle_version="test-v1",
                    kernel_release="kernel-v1",
                    rootfs_release="rootfs-v1",
                    image_sizes={"boot": 1, "init_boot": 2, "vendor_boot": 3, "dtbo": 4},
                )


if __name__ == "__main__":
    unittest.main()
