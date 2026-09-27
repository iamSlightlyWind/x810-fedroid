#!/usr/bin/env python3
"""Host tests for the X810 rootfs tarball install contract."""
import importlib.machinery
import importlib.util
import io
import json
import tempfile
import tarfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("verify-x810-rootfs-archive.py")
LOADER = importlib.machinery.SourceFileLoader("verify_x810_rootfs", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def add_file(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(data))


def make_archive(path, *, fstab=None, port=None, extra=None, os_release_link=True,
                 shadow=b"root:!!:1:0:99999:7:::\n",
                 passwd=b"root:x:0:0:root:/root:/bin/bash\nbin:x:1:1:bin:/bin:/sbin/nologin\nnobody:x:65534:65534:nobody:/:/sbin/nologin\n",
                 hostname=b"localhost.localdomain\n"):
    fstab = fstab or b"PARTLABEL=linuxroot / ext4 defaults 0 0\n"
    port = port or {
        "schema_version": 1, "port_id": "x810-fedora", "name": "Fedora X810",
        "repo_url": "https://github.com/iamSlightlyWind/x810-fedroid",
        "version": "unknown", "device_id": "SM-X810", "os_id": "fedora",
        "os_version": "44", "arch": "aarch64",
    }
    with tarfile.open(path, "w:gz") as archive:
        add_file(archive, "./etc/fstab", fstab)
        if os_release_link:
            link = tarfile.TarInfo("./etc/os-release")
            link.type = tarfile.SYMTYPE
            link.linkname = "../usr/lib/os-release"
            archive.addfile(link)
            add_file(archive, "./usr/lib/os-release", b"NAME=Fedora Linux\nID=fedora\n")
        else:
            add_file(archive, "./etc/os-release", b"NAME=Fedora Linux\nID=fedora\n")
        add_file(archive, "./etc/passwd", passwd)
        add_file(archive, "./etc/shadow", shadow)
        add_file(archive, "./etc/hostname", hostname)
        add_file(archive, "./usr/share/tab-companion/port.json", json.dumps(port).encode())
        add_file(archive, "./usr/lib/modules/7.2.0-gts9wifi/kernel/test.ko", b"module")
        if extra:
            extra(archive)


class RootfsArchiveTests(unittest.TestCase):
    def test_resolves_overlong_relative_symlink_within_mounted_root(self):
        self.assertEqual(
            verifier.resolve_link(
                "usr/bin/gnome-weather",
                "../../../../../../../usr/share/org.gnome.Weather/org.gnome.Weather",
            ),
            "usr/share/org.gnome.Weather/org.gnome.Weather",
        )

    def test_accepts_x810_fedora_rootfs_with_partlabel_root(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "rootfs.tar.gz"
            manifest = Path(temp) / "rootfs-manifest.txt"
            make_archive(archive)
            manifest.write_text("device_id=SM-X810\nos_id=fedora\narch=aarch64\nport_version=unknown\n")
            result = verifier.inspect(str(archive), str(manifest))
            self.assertEqual(result["root"], "PARTLABEL=linuxroot (ext4)")
            self.assertEqual(result["device_id"], "SM-X810")
            self.assertGreater(int(result["kernel_modules"]), 0)

    def test_rejects_stale_uuid_or_separate_boot_fstab(self):
        for fstab in (
            b"UUID=d2a235a8-37cd-4bac-be53-16caf2bfdd21 / ext4 defaults 0 0\n",
            b"PARTLABEL=linuxroot / ext4 defaults 0 0\nUUID=deadbeef /boot ext2 defaults 0 0\n",
        ):
            with self.subTest(fstab=fstab), tempfile.TemporaryDirectory() as temp:
                archive = Path(temp) / "rootfs.tar.gz"
                make_archive(archive, fstab=fstab)
                with self.assertRaises(ValueError):
                    verifier.inspect(str(archive))

    def test_rejects_wrong_device_or_non_fedora(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "rootfs.tar.gz"
            port = {**make_archive.__defaults__[1]} if make_archive.__defaults__ else {}
            port.update({"schema_version": 1, "port_id": "x810-fedora", "device_id": "SM-X710", "os_id": "fedora", "arch": "aarch64", "version": "unknown"})
            make_archive(archive, port=port)
            with self.assertRaisesRegex(ValueError, "schema/target"):
                verifier.inspect(str(archive))

    def test_rejects_unlocked_root_password(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "rootfs.tar.gz"
            make_archive(archive, shadow=b"root:$6$unsafe-password-hash:1:0:99999:7:::\n")
            with self.assertRaisesRegex(ValueError, "root password is not locked"):
                verifier.inspect(str(archive))

    def test_rejects_precreated_human_account(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "rootfs.tar.gz"
            make_archive(archive, passwd=(
                b"root:x:0:0:root:/root:/bin/bash\n"
                b"fedora:x:1000:1000:Fedora:/home/fedora:/bin/bash\n"
            ))
            with self.assertRaisesRegex(ValueError, "pre-created human accounts"):
                verifier.inspect(str(archive))

    def test_rejects_non_neutral_hostname(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "rootfs.tar.gz"
            make_archive(archive, hostname=b"gts9-fedora\n")
            with self.assertRaisesRegex(ValueError, "hostname must be neutral"):
                verifier.inspect(str(archive))

    def test_rejects_cached_dnf_packages_in_rootfs_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "rootfs.tar.gz"

            def add_cached_rpm(tar):
                add_file(tar, "./var/cache/libdnf5/packages/example.rpm", b"cached rpm")

            make_archive(archive, extra=add_cached_rpm)
            with self.assertRaisesRegex(ValueError, "package-manager cache data"):
                verifier.inspect(str(archive))

    def test_rejects_parent_traversal_and_file_beneath_symlink(self):
        cases = (
            lambda archive: add_file(archive, "./../../escape", b"bad"),
            lambda archive: (
                (lambda link: archive.addfile(link))(self._symlink("./shortcut", "usr")),
                add_file(archive, "./shortcut/outside", b"bad"),
            ),
        )
        for add_bad in cases:
            with tempfile.TemporaryDirectory() as temp:
                archive_path = Path(temp) / "rootfs.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    add_file(archive, "./etc/fstab", b"PARTLABEL=linuxroot / ext4 defaults 0 0\n")
                    add_file(archive, "./etc/os-release", b"ID=fedora\n")
                    add_file(archive, "./usr/share/tab-companion/port.json", b"{}")
                    add_file(archive, "./usr/lib/modules/7.2.0-gts9wifi/kernel/test.ko", b"module")
                    add_bad(archive)
                with self.assertRaises(ValueError):
                    verifier.inspect(str(archive_path))

    @staticmethod
    def _symlink(name, target):
        link = tarfile.TarInfo(name)
        link.type = tarfile.SYMTYPE
        link.linkname = target
        return link


if __name__ == "__main__":
    unittest.main()
