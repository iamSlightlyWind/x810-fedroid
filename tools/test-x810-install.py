#!/usr/bin/env python3
"""Unit tests for the read-only Linux installer entry point."""

import importlib.util
import importlib.machinery
import sys
import unittest
import urllib.error
import contextlib
import io
import json
import struct
import tempfile
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch
import subprocess
import tarfile
import hashlib
from contextlib import contextmanager


SCRIPT = Path(__file__).with_name("x810-install")
LOADER = importlib.machinery.SourceFileLoader("x810_install", str(SCRIPT))
SPEC = importlib.util.spec_from_loader("x810_install", LOADER)
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def make_valid_bundle(self, root: Path, boot_sizes=None):
        root.mkdir(parents=True, exist_ok=True)
        boot_sizes = boot_sizes or {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}
        (root / "rootfs").mkdir()
        (root / "kernel").mkdir()
        (root / "boot").mkdir()
        (root / "metadata").mkdir()
        port = json.dumps({"schema_version": 1, "port_id": "x810-fedora", "device_id": "SM-X810",
                           "os_id": "fedora", "os_version": "44", "arch": "aarch64", "version": "1.2.3"})
        rootfs_tar = root / "rootfs/rootfs.tar.gz"
        files = {
            "etc/fstab": "PARTLABEL=linuxroot / ext4 defaults 0 1\n",
            "etc/hostname": "localhost.localdomain\n",
            "etc/shadow": "root:!:1:0:99999:7:::\n",
            "etc/os-release": "ID=fedora\nVERSION_ID=44\n",
            "usr/share/tab-companion/port.json": port,
            "usr/lib/modules/7.2.0-gts9wifi/kernel/example.ko": "module",
            "etc/passwd": "root:x:0:0:root:/root:/bin/bash\n",
        }
        with tarfile.open(rootfs_tar, "w:gz") as archive:
            root_entry = tarfile.TarInfo("./")
            root_entry.type = tarfile.DIRTYPE
            archive.addfile(root_entry)
            for name, content in files.items():
                encoded = content.encode()
                info = tarfile.TarInfo(name)
                info.size = len(encoded)
                info.mode = 0o644
                import io
                archive.addfile(info, io.BytesIO(encoded))
        rpm = root / "kernel/linux-x810.rpm"
        rpm.write_bytes(b"test-kernel-rpm")
        rpm_sha = hashlib.sha256(rpm.read_bytes()).hexdigest()
        firmware_sha = "a" * 64
        root_manifest = root / "rootfs/rootfs-manifest.txt"
        root_manifest.write_text(
            "device_id=SM-X810\nos_id=fedora\narch=aarch64\nport_version=1.2.3\n"
            f"kernel_rpm_sha256={rpm_sha}\nkernel_rpm_nevra=linux-x810-7.2.0-1.aarch64\n"
            f"firmware_sha256={firmware_sha}\n", encoding="utf-8")
        kernel_metadata = root / "kernel/BUILD-METADATA.txt"
        kernel_metadata.write_text(
            f"kernel_rpm_sha256={rpm_sha}\nkernel_rpm_nevra=linux-x810-7.2.0-1.aarch64\n"
            f"firmware_sha256={firmware_sha}\n", encoding="utf-8")
        (root / "metadata/SOURCE-RELEASES.txt").write_text("test source refs\n")
        def record(path):
            data = path.read_bytes()
            return {"file": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data)}
        boot_images = {}
        for name, size in boot_sizes.items():
            path = root / f"boot/{name}.img"
            path.write_bytes((name.encode() + b"_")[:size].ljust(size, b"x"))
            boot_images[name] = record(path)
        artifacts = [rootfs_tar, root_manifest, rpm, kernel_metadata, root / "metadata/SOURCE-RELEASES.txt"]
        artifacts.extend(root / f"boot/{name}.img" for name in boot_sizes)
        manifest = {
            "schema_version": 1, "type": "x810-clean-install", "bundle_version": "test-1",
            "device": {"model": "SM-X810", "codename": "gts9pwifi"},
            "os": {"id": "fedora", "version": "44", "arch": "aarch64"},
            "kernel": {"release": "7.2.0-gts9wifi", "rpm": record(rpm)},
            "rootfs": {"archive": record(rootfs_tar), "manifest": record(root_manifest),
                       "filesystem": "ext4", "minimum_size_bytes": 32 * 1024**3},
            "boot_set": {"images": boot_images},
            "files": {item.relative_to(root).as_posix(): record(item) for item in artifacts},
        }
        (root / installer.CLEAN_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def test_adb_parser_ignores_offline_and_header(self):
        output = "List of devices attached\nusb offline\tunauthorized usb:1\n192.0.2.4:5555\trecovery product:gts9pwifi\n"
        self.assertEqual(installer.adb_serials(output), [("192.0.2.4:5555", "recovery")])

    def test_adb_wait_hint_explains_unauthorized_offline_and_missing_targets(self):
        self.assertIn("RSA prompt", installer.adb_wait_hint(
            "List of devices attached\ntablet unauthorized usb:1\n"))
        self.assertIn("adb kill-server", installer.adb_wait_hint(
            "List of devices attached\ntablet offline usb:1\n"))
        self.assertIn("enable USB debugging", installer.adb_wait_hint(
            "List of devices attached\n"))
        self.assertIn("tablet is not listed", installer.adb_wait_hint(
            "List of devices attached\n", "tablet"))

    def test_host_package_hints_match_common_linux_families(self):
        self.assertIn("dnf install android-tools", installer.package_hint("adb", {"fedora"}))
        self.assertIn("apt install adb", installer.package_hint("adb", {"ubuntu", "debian"}))
        self.assertIn("pacman -S android-tools", installer.package_hint("adb", {"arch"}))
        self.assertIn("coreutils", installer.package_hint("sha256sum", {"fedora"}))

    def test_os_release_reader_does_not_source_shell_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "os-release"
            release.write_text('ID="fedora"\nID_LIKE="rhel centos"\nEVIL=$(touch /tmp/nope)\n')
            self.assertEqual(installer.linux_ids(release), {"fedora", "rhel", "centos"})

    def test_release_check_distinguishes_compact_manifest_from_clean_installer(self):
        checks = installer.release_checks(None)
        self.assertEqual([check.state for check in checks], ["INFO", "BLOCKED"])
        self.assertIn("aggregate release from x810-fedora.yml", checks[0].detail)

        # The release manifest is provenance/checksum metadata, not the
        # Tab Companion port-update feed or a package that installs Fedora.
        release = {"tag_name": "test", "assets": [{"name": name} for name in (
            "manifest.json",
            "x810-fedora-port-1.2.3-1.noarch.rpm", "rootfs.tar.gz",
        )]}
        update, install = installer.release_checks(release)
        self.assertEqual(update.state, "INFO")
        self.assertIn("source commit", update.detail)
        self.assertIn("Tab Companion updates", update.detail)
        self.assertEqual(install.state, "BLOCKED")
        self.assertIn("required rootfs/kernel/boot image assets", install.detail)

    def test_direct_release_assets_enable_installer_status(self):
        release = {"tag_name": "test", "assets": [{"name": name} for name in (
            "manifest.json",
            "update.zip",
            "rootfs.tar.gz", "kernel.rpm",
            "boot.img", "init_boot.img", "vendor_boot.img", "dtbo.img",
        )]}
        update, install = installer.release_checks(release)
        self.assertEqual(update.state, "INFO")
        self.assertEqual(install.state, "CHECK")
        self.assertIn("downloads and verifies", install.detail)

    def test_installer_downloads_individual_verified_assets_from_one_release(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            source = Path(temp)
            local = self.make_valid_bundle(source / "source")
            archive = local / "rootfs/rootfs.tar.gz"
            rpm = local / "kernel/linux-x810.rpm"
            images = {name: local / f"boot/{name}.img" for name in ("boot", "init_boot", "vendor_boot", "dtbo")}
            payloads = {"rootfs.tar.gz": archive.read_bytes(),
                        "kernel.rpm": rpm.read_bytes()}
            payloads.update({f"{name}.img": path.read_bytes() for name, path in images.items()})
            def record(name, data):
                return {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            release_assets = []
            records = []
            for name, data in payloads.items():
                records.append(record(name, data))
                release_assets.append({"name": name, "size": len(data), "browser_download_url": f"https://example.test/{name}"})
            release_manifest = {
                "schema_version": 1, "type": "x810-fedora-release",
                "device": {"model": "SM-X810", "codename": "gts9pwifi"},
                "source_commit": "a" * 40, "release_tag": "test-release",
                "assets": records,
                "components": {
                    "rootfs": {"text_files": {"rootfs-manifest.txt": (local / "rootfs/rootfs-manifest.txt").read_text()}},
                    "kernel": {"text_files": {"BUILD-METADATA.txt": (local / "kernel/BUILD-METADATA.txt").read_text()}},
                },
            }
            manifest_bytes = json.dumps(release_manifest).encode()
            release_assets.append({"name": "manifest.json", "size": len(manifest_bytes),
                                   "browser_download_url": "https://example.test/manifest.json"})
            payload_map = {f"https://example.test/{name}": data for name, data in payloads.items()}
            payload_map["https://example.test/manifest.json"] = manifest_bytes
            class Response(io.BytesIO):
                pass
            def opener(request, timeout=60):
                return Response(payload_map[request.full_url])
            release = {"tag_name": "test-release", "assets": release_assets}
            root = installer.download_release_install_set(release, source / "downloaded", opener)
            bundle = installer.validate_install_bundle(root)
            self.assertEqual(bundle.manifest["source_commit"], "a" * 40)
            self.assertEqual(set(bundle.boot_images), {"boot", "init_boot", "vendor_boot", "dtbo"})
            self.assertEqual(bundle.kernel_rpm.read_bytes(), rpm.read_bytes())

    def test_release_status_tolerates_malformed_asset_metadata(self):
        checks = installer.release_checks({"assets": [None, {"name": []}], "tag_name": "odd"})
        self.assertEqual([check.state for check in checks], ["INFO", "BLOCKED"])

    def test_clean_bundle_manifest_and_build_pair_validate(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            bundle = installer.validate_install_bundle(root)
            self.assertEqual(bundle.manifest["device"]["model"], "SM-X810")
            self.assertEqual(set(bundle.boot_images), {"boot", "init_boot", "vendor_boot", "dtbo"})
            self.assertEqual(bundle.manifest["kernel"]["release"], "7.2.0-gts9wifi")

    def test_clean_bundle_rejects_wrong_target_missing_image_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            path = root / installer.CLEAN_MANIFEST
            doc = json.loads(path.read_text())
            doc["device"]["model"] = "SM-X816B"
            path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, "wrong device"):
                installer.validate_install_bundle(root)
            doc["device"]["model"] = "SM-X810"
            doc["boot_set"]["images"]["recovery"] = doc["boot_set"]["images"].pop("dtbo")
            path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, "exactly boot"):
                installer.validate_install_bundle(root)
            doc["boot_set"]["images"].pop("recovery")
            path.write_text(json.dumps(doc))
            archive_path = root / doc["rootfs"]["archive"]["file"]
            archive_path.write_bytes(b"z" * archive_path.stat().st_size)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                installer.validate_install_bundle(root)

    def test_bundle_path_traversal_and_symlink_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "traversal"):
            installer.normalize_bundle_path("../../escape")
        with tempfile.TemporaryDirectory() as temp:
            outer = Path(temp) / "bad.tar.gz"
            with tarfile.open(outer, "w:gz") as archive:
                import io
                info = tarfile.TarInfo("../escape")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            with self.assertRaisesRegex(ValueError, "traversal"):
                installer.extract_clean_bundle(outer, Path(temp) / "extract")

    def test_android_preflight_requires_exact_identity_unlocked_and_root(self):
        properties = {
            "ro.boot.em.model": "SM-X810", "ro.product.device": "gts9pwifi",
            "ro.boot.flash.locked": "0", "ro.boot.vbmeta.device_state": "unlocked",
            "ro.boot.verifiedbootstate": "orange", "ro.bootloader": "X810XXS5CYG1",
        }
        calls = []
        def fake_runner(argv, timeout=15):
            calls.append(argv)
            if argv[1:] == ["devices", "-l"]:
                return subprocess.CompletedProcess(argv, 0, "List of devices attached\nserial\tdevice product:gts9pwifi\n", "")
            if argv[-2:] == ["su", "-c"]:
                raise AssertionError("unexpected argv");
            if "su" in argv:
                return subprocess.CompletedProcess(argv, 0, "0\n", "")
            return subprocess.CompletedProcess(argv, 0, properties.get(argv[-1], "") + "\n", "")
        serial, checks = installer.android_preflight("adb", None, fake_runner)
        self.assertEqual(serial, "serial")
        self.assertTrue(all(check.state == "OK" for check in checks))
        self.assertFalse(any("reboot" in argv for argv in calls))

    def test_android_preflight_stops_if_locked_unknown_or_su_denied(self):
        def runner_for(props, root_result=0):
            def run(argv, timeout=15):
                if "devices" in argv:
                    return subprocess.CompletedProcess(argv, 0, "List of devices attached\nserial\tdevice\n", "")
                if "su" in argv:
                    return subprocess.CompletedProcess(argv, root_result, "1000\n" if root_result else "0\n", "")
                return subprocess.CompletedProcess(argv, 0, props.get(argv[-1], "") + "\n", "")
            return run
        base = {"ro.boot.em.model": "SM-X810", "ro.product.device": "gts9pwifi"}
        locked, checks = installer.android_preflight("adb", None,
            runner_for(base | {"ro.boot.flash.locked": "1", "ro.boot.vbmeta.device_state": "locked"}))
        self.assertTrue(any(c.label == "Bootloader" and c.state == "STOP" for c in checks))
        unproven, checks = installer.android_preflight("adb", None, runner_for(base))
        self.assertTrue(any(c.label == "Bootloader" and c.state == "STOP" for c in checks))
        denied, checks = installer.android_preflight("adb", None,
            runner_for(base | {"ro.boot.flash.locked": "0"}, root_result=1))
        self.assertTrue(any(c.label == "Android root" and c.state == "STOP" for c in checks))

    def test_android_and_fedora_size_requests_leave_remainder_unpartitioned(self):
        plan = installer.calculate_install_split("80GiB", "60GiB", 40 * 1024**3)
        self.assertGreater(plan["free_sectors"], 0)
        self.assertEqual(plan["userdata_first"], installer.X810_USERDATA_START)
        self.assertEqual(plan["linuxroot_first"], plan["userdata_last"] + 1)
        self.assertEqual(plan["linuxroot_last"] + plan["free_sectors"], installer.X810_USERDATA_END)
        pct = installer.calculate_install_split("40%", "25%", 32 * 1024**3)
        self.assertGreater(pct["free_sectors"], 0)
        with self.assertRaisesRegex(ValueError, "exceed"):
            installer.calculate_install_split("70%", "40%", 32 * 1024**3)
        with self.assertRaisesRegex(ValueError, "at least 32 GiB"):
            installer.calculate_install_split("20GiB", "50GiB", 32 * 1024**3)
        with self.assertRaisesRegex(ValueError, "at least"):
            installer.calculate_install_split("80GiB", "35GiB", 40 * 1024**3)

    def test_stock_gpt_writer_changes_only_entries_34_and_35_and_uses_distinct_guid(self):
        plan = installer.calculate_install_split("80GiB", "60GiB", 32 * 1024**3)
        stock = {"state": "stock", "userdata": {"first": str(installer.X810_USERDATA_START),
                 "last": str(installer.X810_USERDATA_END)}, "disk_last": installer.X810_USERDATA_END}
        split = {"state": "split", "userdata": {"name": "userdata", "first": str(plan["userdata_first"]),
                 "last": str(plan["userdata_last"])}, "linuxroot": {"name": "linuxroot",
                 "first": str(plan["linuxroot_first"]), "last": str(plan["linuxroot_last"])},
                 "disk_last": installer.X810_USERDATA_END}
        calls = []
        class Client:
            adb, serial = "adb", "serial"
            runner = staticmethod(lambda argv, timeout=15: subprocess.CompletedProcess(argv, 0, "", ""))
            def shell(self, *args, timeout=30):
                calls.append(args)
                if args[:3] == ("sh", "-c", "grep -qE ' /data ' /proc/mounts"):
                    return subprocess.CompletedProcess(args, 1, "", "not mounted")
                return subprocess.CompletedProcess(args, 0, "", "")
        with patch.object(installer, "twrp_gpt_layout", side_effect=[
            (stock, [installer.Check("GPT", "OK", "stock")]),
            (split, [installer.Check("GPT", "OK", "split")]),
        ]):
            checks = installer.install_partition_table(Client(), stock, plan, installer.INSTALL_GPT_CONFIRMATION)
        self.assertFalse(any(check.state == "STOP" for check in checks))
        write = next(call for call in calls if call and call[0] == "sgdisk")
        self.assertIn("--delete=34", write)
        self.assertFalse(any(arg == "--delete=35" for arg in write))
        guid = next(arg.split(":", 1)[1] for arg in write if arg.startswith("--partition-guid=34:"))
        self.assertNotEqual(guid, installer.X810_USERDATA_TYPE_GUID)
        self.assertEqual(write[-1], "/dev/block/sda")
        self.assertFalse(any("recovery" in str(call) or "vbmeta" in str(call) or "reboot" in str(call) for call in calls))

    def make_checkpoint_backup(self, backup_dir, bundle, split_plan, sizes=None):
        sizes = sizes or {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}
        backup_dir.mkdir(mode=0o700, parents=True)
        (backup_dir / "boot").mkdir()
        (backup_dir / "gpt").mkdir()
        boot_records = {}
        for name, size in sizes.items():
            path = backup_dir / "boot" / f"{name}.img"
            path.write_bytes((name.encode() + b"_")[:size].ljust(size, b"x"))
            boot_records[name] = {"file": f"boot/{name}.img", "size_bytes": size,
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        boot_manifest = backup_dir / "boot-backup-manifest.json"
        boot_manifest.write_text(json.dumps({"schema_version": 1, "model": "SM-X810", "images": boot_records}))
        (backup_dir / "boot-backup-manifest.sha256").write_text(
            f"{hashlib.sha256(boot_manifest.read_bytes()).hexdigest()}  boot-backup-manifest.json\n")
        gpt_payload = backup_dir / "gpt" / "sda-first.bin"
        gpt_payload.write_bytes(b"synthetic GPT capture")
        gpt_manifest = backup_dir / "gpt" / "manifest.json"
        gpt_manifest.write_text(json.dumps({"schema_version": 1, "device_model": "SM-X810",
                                             "sha256": {"sda-first.bin": hashlib.sha256(gpt_payload.read_bytes()).hexdigest()}}))
        return installer.write_install_checkpoint(backup_dir, "serial", bundle, "3.7.0", split_plan)

    def test_resume_checkpoint_binds_device_bundle_geometry_and_local_backup_hashes(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            bundle = installer.validate_install_bundle(root)
            split = installer.calculate_install_split("80GiB", "60GiB", 32 * 1024**3)
            backup = Path(temp) / "backup"
            self.make_checkpoint_backup(backup, bundle, split)
            checkpoint = installer.validate_resume_checkpoint(backup, "serial", bundle)
            self.assertEqual(checkpoint["split"]["linuxroot_last"], split["linuxroot_last"])
            self.assertNotIn("password", json.dumps(checkpoint).lower())
            with self.assertRaisesRegex(ValueError, "serial"):
                installer.validate_resume_checkpoint(backup, "other-serial", bundle)
            changed = dict(bundle.manifest)
            changed["bundle_version"] = "same?"
            bad_bundle = installer.InstallBundle(bundle.root, changed, bundle.rootfs_archive,
                bundle.rootfs_manifest, bundle.boot_images, bundle.kernel_rpm, bundle.kernel_metadata)
            with self.assertRaisesRegex(ValueError, "bundle"):
                installer.validate_resume_checkpoint(backup, "serial", bad_bundle)
            (backup / "boot" / "boot.img").write_bytes(b"corrupt!")
            with self.assertRaisesRegex(ValueError, "missing/corrupt"):
                installer.validate_resume_checkpoint(backup, "serial", bundle)

    def test_manual_userdata_format_is_human_confirmed_and_never_commanded(self):
        split = installer.calculate_install_split("80GiB", "60GiB", 32 * 1024**3)
        checkpoint = {"split": split}
        calls = []
        class FakeClient:
            def shell(self, *args, timeout=30):
                calls.append(args)
                if args[:2] == ("blockdev", "--getsize64"):
                    size = (split["userdata_last"] - split["userdata_first"] + 1) * installer.GPT_CAPTURE_BLOCK
                    return subprocess.CompletedProcess(args, 0, str(size), "")
                if args[:1] == ("blkid",):
                    return subprocess.CompletedProcess(args, 0, "ext4\n", "")
                raise AssertionError(f"unexpected command: {args}")
        with self.assertRaisesRegex(RuntimeError, "confirmation"):
            installer.require_manual_userdata_format(FakeClient(), checkpoint,
                input_func=lambda _: "")
        self.assertEqual(calls, [])
        installer.require_manual_userdata_format(FakeClient(), checkpoint,
            input_func=lambda _: "I FORMATTED ANDROID USERDATA IN TWRP")
        self.assertTrue(all("mke2fs" not in str(call) and "wipe" not in str(call) for call in calls))

    def test_twrp_utility_preflight_stops_before_operations_if_any_missing(self):
        class FakeClient:
            def shell(self, *args, timeout=30):
                return subprocess.CompletedProcess(args, 0, "MISSING:mke2fs\n", "")
        checks = installer.preflight_twrp_tools(FakeClient())
        self.assertEqual(checks[0].state, "STOP")
        self.assertIn("mke2fs", checks[0].detail)

    def test_fresh_stock_flow_stops_after_gpt_and_saves_credential_free_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            bundle = installer.validate_install_bundle(root)
            @contextmanager
            def opened(_): yield bundle
            stock = {"state": "stock", "userdata": {"first": str(installer.X810_USERDATA_START),
                     "last": str(installer.X810_USERDATA_END)}, "disk_last": installer.X810_USERDATA_END}
            backup_dir = Path(temp) / "new-install-backup"
            inputs = iter(["", "80GiB", "60GiB", installer.INSTALL_CONFIRMATION,
                           installer.INSTALL_GPT_CONFIRMATION])
            events = []
            def backup_boot(_client, out):
                out.joinpath("boot").mkdir()
                records = {}
                for name, size in installer.INSTALL_BOOT_IMAGE_SIZES.items():
                    path = out / "boot" / f"{name}.img"
                    path.write_bytes(b"x" * size)
                    records[name] = {"file": f"boot/{name}.img", "size_bytes": size,
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                manifest = out / "boot-backup-manifest.json"
                manifest.write_text(json.dumps({"schema_version": 1, "model": "SM-X810", "images": records}))
                (out / "boot-backup-manifest.sha256").write_text(
                    f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  boot-backup-manifest.json\n")
                return [installer.Check("Boot backup", "OK", "saved")]
            def backup_gpt(_adb, _serial, out, *_args, **_kwargs):
                out.mkdir()
                payload = out / "capture.bin"
                payload.write_bytes(b"synthetic GPT capture")
                (out / "manifest.json").write_text(json.dumps({"schema_version": 1, "device_model": "SM-X810",
                    "sha256": {"capture.bin": hashlib.sha256(payload.read_bytes()).hexdigest()}}))
                return [installer.Check("GPT backup", "OK", "saved")]
            def write_gpt(*_args, **_kwargs):
                checkpoint = json.loads((backup_dir / "install-checkpoint.json").read_text())
                self.assertNotIn("password", json.dumps(checkpoint).lower())
                events.append("gpt-write")
                return [installer.Check("GPT write", "OK", "verified")]
            with patch.object(installer.platform, "system", return_value="Linux"), \
                 patch.object(installer.shutil, "which", return_value="/usr/bin/adb"), \
                 patch.object(installer, "open_install_bundle", opened), \
                 patch.object(installer, "android_preflight", return_value=("serial", [installer.Check("Android", "OK", "ready")])), \
                 patch.object(installer, "adb_target", return_value=("serial", "recovery")), \
                 patch.object(installer, "twrp_identity", return_value=({"twrp": "3.7.0"}, [installer.Check("TWRP", "OK", "root")])), \
                 patch.object(installer, "preflight_twrp_tools", return_value=[installer.Check("tools", "OK", "ready")]), \
                 patch.object(installer, "twrp_gpt_layout", return_value=(stock, [installer.Check("GPT", "OK", "stock")])), \
                 patch.object(installer, "default_install_backup_path", return_value=backup_dir), \
                 patch.object(installer, "save_current_boot_set", side_effect=backup_boot), \
                 patch.object(installer, "save_gpt_backup", side_effect=backup_gpt), \
                 patch.object(installer, "install_partition_table", side_effect=write_gpt), \
                 patch.object(installer, "format_and_install_rootfs") as format_root, \
                 patch.object(installer, "prompt_install_details", return_value={"username": "alice", "full_name": "Alice", "hostname": "tablet", "password_hash": "$6$secret"}):
                status = installer.run_install(Path("bundle"), runner=lambda *a, **k: None,
                                               input_func=lambda _prompt: next(inputs))
            self.assertEqual(status, 3)
            self.assertEqual(events, ["gpt-write"])
            format_root.assert_not_called()
            checkpoint = json.loads((backup_dir / "install-checkpoint.json").read_text())
            self.assertEqual(checkpoint["serial"], "serial")
            self.assertEqual(checkpoint["bundle_manifest_sha256"], hashlib.sha256((root / installer.CLEAN_MANIFEST).read_bytes()).hexdigest())
            self.assertNotIn("secret", json.dumps(checkpoint))

    def test_resume_flow_requires_exact_split_and_manual_format_then_writes_rootfs(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            bundle = installer.validate_install_bundle(root)
            split = installer.calculate_install_split("80GiB", "60GiB", 32 * 1024**3)
            backup = Path(temp) / "backup"
            self.make_checkpoint_backup(backup, bundle, split)
            @contextmanager
            def opened(_): yield bundle
            layout = {"state": "split",
                      "userdata": {"first": str(split["userdata_first"]), "last": str(split["userdata_last"])},
                      "linuxroot": {"first": str(split["linuxroot_first"]), "last": str(split["linuxroot_last"])}}
            inputs = iter(["INSTALL FEDORA ON SM-X810 AND ERASE LINUXROOT",
                           "FORMAT LINUXROOT AND FLASH FOUR BOOT IMAGES"])
            with patch.object(installer.platform, "system", return_value="Linux"), \
                 patch.object(installer.shutil, "which", return_value="/usr/bin/adb"), \
                 patch.object(installer, "open_install_bundle", opened), \
                 patch.object(installer, "adb_target", return_value=("serial", "recovery")), \
                 patch.object(installer, "twrp_identity", return_value=({"twrp": "3.7.0"}, [installer.Check("TWRP", "OK", "root")])), \
                 patch.object(installer, "preflight_twrp_tools", return_value=[installer.Check("tools", "OK", "ready")]), \
                 patch.object(installer, "twrp_gpt_layout", return_value=(layout, [installer.Check("GPT", "OK", "split")])), \
                 patch.object(installer, "verify_current_boot_set_matches_backup") as verify_boots, \
                 patch.object(installer, "prompt_install_details", return_value={"username": "alice", "full_name": "Alice", "hostname": "tablet", "password_hash": "$6$hash"}), \
                 patch.object(installer, "require_manual_userdata_format") as manual_data, \
                 patch.object(installer, "format_and_install_rootfs") as install_root:
                status = installer.run_install(Path("bundle"), resume_from=backup,
                                               runner=lambda *a, **k: None,
                                               input_func=lambda _prompt: next(inputs))
            self.assertEqual(status, 0)
            verify_boots.assert_called_once()
            manual_data.assert_called_once()
            install_root.assert_called_once()
            self.assertEqual(install_root.call_args.args[2], split["linuxroot_sectors"] * installer.GPT_CAPTURE_BLOCK)

            wrong = dict(layout)
            wrong["linuxroot"] = dict(layout["linuxroot"], last=str(int(layout["linuxroot"]["last"]) - 1))
            with patch.object(installer.platform, "system", return_value="Linux"), \
                 patch.object(installer.shutil, "which", return_value="/usr/bin/adb"), \
                 patch.object(installer, "open_install_bundle", opened), \
                 patch.object(installer, "adb_target", return_value=("serial", "recovery")), \
                 patch.object(installer, "twrp_identity", return_value=({"twrp": "3.7.0"}, [installer.Check("TWRP", "OK", "root")])), \
                 patch.object(installer, "preflight_twrp_tools", return_value=[installer.Check("tools", "OK", "ready")]), \
                 patch.object(installer, "twrp_gpt_layout", return_value=(wrong, [installer.Check("GPT", "OK", "split")])), \
                 patch.object(installer, "format_and_install_rootfs") as refused:
                status = installer.run_install(Path("bundle"), resume_from=backup,
                                               runner=lambda *a, **k: None,
                                               input_func=lambda _prompt: "")
            self.assertEqual(status, 2)
            refused.assert_not_called()

    def test_restore_boot_set_requires_manifest_hash_device_sizes_and_confirmation(self):
        sizes = {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES, sizes):
            backup = Path(temp)
            (backup / "boot").mkdir()
            images = {}
            blobs = {}
            for name in sizes:
                data = (name.encode() + b"_")[:8].ljust(8, b"x")
                blobs[name] = data
                path = backup / "boot" / f"{name}.img"
                path.write_bytes(data)
                images[name] = {"file": f"boot/{name}.img", "size_bytes": 8,
                                "sha256": hashlib.sha256(data).hexdigest()}
            manifest_path = backup / "boot-backup-manifest.json"
            manifest_path.write_text(json.dumps({"schema_version": 1, "model": "SM-X810", "images": images}))
            (backup / "boot-backup-manifest.sha256").write_text(
                f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  boot-backup-manifest.json\n")
            calls = []
            def runner(argv, timeout=15):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, "8\n", "")
            def binary_reader(_adb, _serial, remote, timeout=45):
                name = next(item for item in sizes if f"by-name/{item}" in remote)
                return blobs[name]
            def stream_runner(_adb, _serial, source, remote, timeout=1800):
                calls.append(["stream", source.name, remote])
                return subprocess.CompletedProcess([], 0, "", "")
            valid_layout = ("split", {"first": str(installer.X810_USERDATA_START),
                                     "last": str(installer.X810_USERDATA_START + 1000)},
                            {"first": str(installer.X810_USERDATA_START + 1001),
                             "last": str(installer.X810_USERDATA_END)})
            layout = {"state": valid_layout[0], "userdata": valid_layout[1], "linuxroot": valid_layout[2]}
            with patch.object(installer, "adb_target", return_value=("serial", "recovery")), \
                 patch.object(installer, "twrp_identity", return_value=({"model": "SM-X810"}, [installer.Check("TWRP", "OK", "root")])), \
                 patch.object(installer, "twrp_gpt_layout", return_value=(layout, [installer.Check("GPT", "OK", "split")])):
                checks = installer.restore_boot_set_from_backup("adb", "serial", backup, runner,
                    binary_reader, stream_runner, input_func=lambda _: "RESTORE FOUR SM-X810 BOOT IMAGES")
            self.assertEqual([check.state for check in checks], ["OK"] * 4)
            writes = [call for call in calls if call[0] == "stream"]
            self.assertEqual([call[2].split("by-name/")[-1].split()[0] for call in writes], list(sizes))
            self.assertFalse(any("recovery" in str(call) or "vbmeta" in str(call) or "reboot" in str(call) for call in calls))
            (backup / "boot-backup-manifest.sha256").write_text("0" * 64 + "  boot-backup-manifest.json\n")
            calls.clear()
            with patch.object(installer, "adb_target", return_value=("serial", "recovery")), \
                 patch.object(installer, "twrp_identity", return_value=({"model": "SM-X810"}, [installer.Check("TWRP", "OK", "root")])), \
                 patch.object(installer, "twrp_gpt_layout", return_value=(layout, [installer.Check("GPT", "OK", "split")])):
                checks = installer.restore_boot_set_from_backup("adb", "serial", backup, runner,
                    binary_reader, stream_runner, input_func=lambda _: "RESTORE FOUR SM-X810 BOOT IMAGES")
            self.assertEqual(checks[0].state, "STOP")
            self.assertFalse(any(call[0] == "stream" for call in calls))

    def test_partition_confirmation_failure_makes_no_device_call(self):
        class Client:
            adb, serial, runner = "adb", "serial", lambda *args, **kwargs: None
            def shell(self, *args, **kwargs): raise AssertionError("no adb call expected")
        checks = installer.install_partition_table(Client(), {"state": "stock"}, {}, "wrong")
        self.assertEqual(checks[0].state, "STOP")

    def test_plan_only_install_is_read_only_even_with_mocked_device(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_valid_bundle(Path(temp) / "bundle")
            (root / "rootfs/rootfs.tar.gz").touch(exist_ok=True)
            bundle = installer.InstallBundle(
                root=root, manifest={"bundle_version": "test", "rootfs": {"minimum_size_bytes": 32 * 1024**3},
                                     "kernel": {"release": "7.2.0-gts9wifi"}},
                rootfs_archive=root / "rootfs/rootfs.tar.gz", rootfs_manifest=root / "rootfs/rootfs-manifest.txt",
                boot_images={}, kernel_rpm=root / "kernel/linux-x810.rpm",
                kernel_metadata=root / "kernel/BUILD-METADATA.txt")
            @contextmanager
            def opened(_): yield bundle
            stock = {"state": "stock", "userdata": {"first": str(installer.X810_USERDATA_START),
                     "last": str(installer.X810_USERDATA_END)}, "disk_last": installer.X810_USERDATA_END}
            inputs = iter(["80GiB", "60GiB"])
            with patch.object(installer.platform, "system", return_value="Linux"), \
                 patch.object(installer.shutil, "which", return_value="/usr/bin/adb"), \
                 patch.object(installer, "open_install_bundle", opened), \
                 patch.object(installer, "android_preflight", return_value=("serial", [installer.Check("Android", "OK", "ready")])), \
                 patch.object(installer, "adb_target", return_value=("serial", "recovery")), \
                 patch.object(installer, "twrp_identity", return_value=({"twrp": "3.7", "device": "gts9pwifi"}, [installer.Check("TWRP", "OK", "root")])), \
                 patch.object(installer, "preflight_twrp_tools", return_value=[installer.Check("TWRP utilities", "OK", "ready")]), \
                 patch.object(installer, "twrp_gpt_layout", return_value=(stock, [installer.Check("GPT", "OK", "stock")])), \
                 patch.object(installer, "save_current_boot_set") as boot_backup, \
                 patch.object(installer, "save_gpt_backup") as gpt_backup, \
                 patch.object(installer, "install_partition_table") as gpt_write, \
                 patch.object(installer, "format_and_install_rootfs") as install_fs:
                status = installer.run_install(None, plan_only=True, runner=lambda *a, **k: None,
                                               input_func=lambda prompt: "" if "TWRP" in prompt else next(inputs))
            boot_backup.assert_not_called(); gpt_backup.assert_not_called()
            gpt_write.assert_not_called(); install_fs.assert_not_called()
            self.assertEqual(status, 0)

    def test_rootfs_and_four_boot_writes_are_readback_checked_with_fake_adb(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            bundle = installer.validate_install_bundle(root)
            backup = Path(temp) / "backup"
            split = installer.calculate_install_split("80GiB", "60GiB", 32 * 1024**3)
            self.make_checkpoint_backup(backup, bundle, split)
            calls = []
            pushed = {}
            class FakeClient:
                def shell(self, *args, timeout=30):
                    calls.append(("shell", args))
                    if args[:2] == ("blockdev", "--getsize64"):
                        return subprocess.CompletedProcess(args, 0, str(64 * 1024**3), "")
                    return subprocess.CompletedProcess(args, 0, "", "")
                def push(self, source, remote_path, timeout=60):
                    calls.append(("push", source.name, remote_path))
                    pushed[remote_path] = source.read_bytes()
                    return subprocess.CompletedProcess([], 0, "", "")
                def stream(self, source, remote, timeout=1800):
                    calls.append(("stream", source.name, remote))
                    return subprocess.CompletedProcess([], 0, "", "")
                def read_binary(self, remote, timeout=180):
                    if "by-name/" in remote:
                        name = next(item for item in installer.INSTALL_BOOT_IMAGE_SIZES if f"by-name/{item}" in remote)
                        return (root / f"boot/{name}.img").read_bytes()
                    path = next(path for path in pushed if path in remote)
                    return pushed[path]
            with patch.object(installer, "provision_rootfs_account") as provision:
                installer.format_and_install_rootfs(FakeClient(), bundle, 64 * 1024**3,
                                                     {"username": "tester", "hostname": "test", "full_name": "Test Person", "password_hash": "$6$hash"},
                                                     backup)
            provision.assert_called_once()
            streams = [call for call in calls if call[0] == "stream"]
            self.assertEqual([call[1] for call in streams[1:]], ["boot.img", "init_boot.img", "vendor_boot.img", "dtbo.img"])
            self.assertEqual({path.split("/")[-2] for path in pushed}, {"android", "fedora"})
            self.assertEqual(len(pushed), 8)
            for path, data in pushed.items():
                self.assertEqual(len(data), 8)
                self.assertTrue(path.endswith(".img"))
                name = Path(path).name
                if "/android/" in path:
                    expected = (backup / "boot" / name).read_bytes()
                else:
                    expected = (root / "boot" / name).read_bytes()
                self.assertEqual(data, expected)
            self.assertTrue(any("/android/name.txt" in str(call) for call in calls))
            self.assertTrue(any("/fedora/name.txt" in str(call) for call in calls))
            self.assertFalse(any(call[1][:2] == ("twrp", "wipe") for call in calls if call[0] == "shell"))
            self.assertFalse(any("userdata" in str(call) and "mke2fs" in str(call) for call in calls))
            self.assertTrue(any(call[1][:1] == ("mke2fs",) for call in calls if call[0] == "shell"))
            self.assertFalse(any("recovery" in str(call) or "vbmeta" in str(call) or "reboot" in str(call) for call in calls))

    def test_boot_set_seed_fails_closed_on_readback_mismatch(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            paths = {}
            for name in installer.INSTALL_BOOT_IMAGE_SIZES:
                path = Path(temp) / f"{name}.img"
                path.write_bytes(b"expected")
                paths[name] = path

            class CorruptingClient:
                def shell(self, *args, timeout=30):
                    return subprocess.CompletedProcess(args, 0, "", "")
                def push(self, source, remote_path, timeout=60):
                    return subprocess.CompletedProcess([], 0, "", "")
                def read_binary(self, remote, timeout=180):
                    return b"corrupt!"

            with self.assertRaisesRegex(RuntimeError, "read-back verification"):
                installer.stage_boot_set_files(CorruptingClient(), "/tmp/root", "fedora", "Fedora", paths)

    def test_rootfs_write_failure_stops_before_boot_writes(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            bundle = installer.validate_install_bundle(root)
            backup = Path(temp) / "backup"
            split = installer.calculate_install_split("80GiB", "60GiB", 32 * 1024**3)
            self.make_checkpoint_backup(backup, bundle, split)
            class BrokenClient:
                streams = []
                def shell(self, *args, timeout=30):
                    if args[:2] == ("blockdev", "--getsize64"): return subprocess.CompletedProcess(args, 0, str(64 * 1024**3), "")
                    return subprocess.CompletedProcess(args, 0, "", "")
                def stream(self, source, remote, timeout=1800):
                    self.streams.append(source.name)
                    return subprocess.CompletedProcess([], 1, "", "rootfs extraction failed")
                def read_binary(self, *args, **kwargs): raise AssertionError("no boot readback expected")
            client = BrokenClient()
            with patch.object(installer, "provision_rootfs_account") as provision:
                with self.assertRaisesRegex(RuntimeError, "rootfs extraction failed"):
                    installer.format_and_install_rootfs(client, bundle, 64 * 1024**3,
                        {"username": "tester", "hostname": "test", "full_name": "Test", "password_hash": "$6$hash"}, backup)
            self.assertEqual(client.streams, ["rootfs.tar.gz"])
            provision.assert_not_called()

    def test_account_setup_never_stages_plaintext_password(self):
        calls = []
        class FakeClient:
            def push(self, source, remote_path, timeout=60):
                content = source.read_text()
                calls.append(("push", remote_path, content))
                return subprocess.CompletedProcess([], 0, "", "")
            def shell(self, *args, timeout=30):
                calls.append(("shell", args))
                return subprocess.CompletedProcess([], 0, "", "")
        installer.provision_rootfs_account(FakeClient(), "/tmp/x810-root", "alice",
                                           "Alice Example", "tablet", "$6$test-hash")
        script = next(call[2] for call in calls if call[:2] == ("push", "/tmp/x810-install-provision.sh"))
        passwd_hash = next(call[2] for call in calls if call[:2] == ("push", "/tmp/x810-install-password"))
        self.assertIn("/usr/sbin/useradd", script)
        self.assertIn("--groups wheel,video,input", script)
        self.assertIn("--lock root", script)
        self.assertIn("$6$test-hash", passwd_hash)
        self.assertNotIn("plain-password", repr(calls))
        self.assertFalse(any("reboot" in str(call) for call in calls))

    def test_account_setup_rejects_colon_in_gecos_name(self):
        class NoCallClient:
            def push(self, *args, **kwargs):
                raise AssertionError("invalid name must be rejected before staging")
        with self.assertRaisesRegex(ValueError, "invalid full name"):
            installer.provision_rootfs_account(NoCallClient(), "/tmp/x810-root", "alice",
                                               "Alice:root", "tablet", "$6$test-hash")

    def test_http_404_means_no_release(self):
        def missing(*args, **kwargs):
            raise urllib.error.HTTPError("url", 404, "missing", {}, None)
        self.assertIsNone(installer.latest_release(missing))

    def test_text_menu_exits_cleanly(self):
        output = io.StringIO()
        with patch("builtins.input", return_value="7"), \
             patch.object(installer, "run_split") as split, \
             contextlib.redirect_stdout(output):
            self.assertEqual(installer.menu(), 0)
        split.assert_not_called()
        self.assertIn("Install Fedora (guided", output.getvalue())
        self.assertIn("7) Quit", output.getvalue())
        self.assertNotIn("Type ERASE-ANDROID-USERDATA", output.getvalue())

    def test_text_menu_split_preview_route_never_calls_writer(self):
        output = io.StringIO()
        with patch("builtins.input", side_effect=["6", "capture/gpt", "50", "7"]), \
             patch.object(installer, "run_split_plan", return_value=0) as plan, \
             patch.object(installer, "run_split") as writer, \
             contextlib.redirect_stdout(output):
            self.assertEqual(installer.menu(), 0)
        plan.assert_called_once_with(Path("capture/gpt"), 50)
        writer.assert_not_called()
        self.assertIn("6) Preview a split from saved GPT metadata (read-only)", output.getvalue())
        self.assertNotIn("--write", output.getvalue())
        self.assertNotIn("ERASE-ANDROID-USERDATA", output.getvalue())

    def test_split_plan_uses_read_only_capture_planner(self):
        result = subprocess.CompletedProcess([], 0, "READ-ONLY X810 plan\nNo tablet or GPT was modified.\n", "")
        with patch.object(installer, "command", return_value=result) as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(installer.run_split_plan(Path("capture/gpt"), 60), 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(Path(argv[1]).name, "plan-x810-split.py")
        self.assertIn("capture/gpt", argv)
        self.assertIn("60", argv)
        self.assertIn("No tablet or GPT was modified", output.getvalue())

    def test_live_split_arithmetic_matches_captured_x810_planner(self):
        plan = installer.calculate_split(60)
        self.assertEqual(plan["userdata_first"], 3_568_128)
        self.assertEqual(plan["userdata_last"], 38_792_191)
        self.assertEqual(plan["linuxroot_first"], 38_792_192)
        self.assertEqual(plan["linuxroot_last"], 62_275_574)
        self.assertEqual(plan["userdata_sectors"] + plan["linuxroot_sectors"],
                         installer.X810_USERDATA_END - installer.X810_USERDATA_START + 1)
        self.assertEqual(plan["linuxroot_first"] % installer.X810_SPLIT_ALIGNMENT_SECTORS, 0)
        with self.assertRaises(ValueError):
            installer.calculate_split(4)

    def test_sgdisk_partition_info_parser(self):
        info = """Partition GUID code: 1B81E7E6-F50D-419B-A739-2AEEF8DA3335 (Android data)
Partition unique GUID: 1B81E7E6-F50D-419B-A739-2AEEF8DA3335
First sector: 3568128 (at 13.6 GiB)
Last sector: 62275574 (at 237.0 GiB)
Partition name: 'userdata'
"""
        parsed = installer.parse_sgdisk_info(info)
        self.assertEqual(parsed["name"], "userdata")
        self.assertEqual(parsed["first"], "3568128")
        self.assertEqual(parsed["last"], "62275574")
        self.assertIsNone(installer.parse_sgdisk_info("Partition #35 does not exist.\n"))

    def test_live_twrp_layout_accepts_exact_measured_stock_table(self):
        userdata = """Partition GUID code: 1B81E7E6-F50D-419B-A739-2AEEF8DA3335 (Android data)
Partition unique GUID: 1B81E7E6-F50D-419B-A739-2AEEF8DA3335
First sector: 3568128
Last sector: 62275574
Partition name: 'userdata'
"""
        def fake_command(argv, timeout=15):
            if "--getsize64" in argv:
                return subprocess.CompletedProcess(argv, 0, str(installer.X810_LUNS["sda"]), "")
            if "logical_block_size" in argv[-1]:
                return subprocess.CompletedProcess(argv, 0, "4096", "")
            if "--print" in argv:
                return subprocess.CompletedProcess(argv, 0, f"Disk identifier (GUID): {installer.X810_INTERNAL_DISK_GUID}\n", "")
            if "--verify" in argv:
                return subprocess.CompletedProcess(argv, 0, "No problems found.\n", "")
            if "--info=34" in argv:
                return subprocess.CompletedProcess(argv, 0, userdata, "")
            number = next((n for n in range(35, 41) if f"--info={n}" in argv), None)
            if number is not None:
                return subprocess.CompletedProcess(argv, 1, f"Partition #{number} does not exist.\n", "")
            raise AssertionError(argv)
        with patch.object(installer, "command", side_effect=fake_command):
            layout, checks = installer.twrp_gpt_layout("adb", "serial")
        self.assertEqual(layout["state"], "stock")
        self.assertTrue(all(check.state == "OK" for check in checks))

    def test_live_twrp_layout_rejects_wrong_internal_disk_identity(self):
        def fake_command(argv, timeout=15):
            if "--getsize64" in argv:
                return subprocess.CompletedProcess(argv, 0, str(installer.X810_LUNS["sda"]), "")
            if "logical_block_size" in argv[-1]:
                return subprocess.CompletedProcess(argv, 0, "4096", "")
            if "--print" in argv:
                return subprocess.CompletedProcess(argv, 0, "Disk identifier (GUID): 00000000-0000-0000-0000-000000000000\n", "")
            if "--verify" in argv:
                return subprocess.CompletedProcess(argv, 0, "No problems found.\n", "")
            raise AssertionError("wrong disk identity must stop before reading partition entries")
        with patch.object(installer, "command", side_effect=fake_command):
            layout, checks = installer.twrp_gpt_layout("adb", "serial")
        self.assertIsNone(layout)
        self.assertEqual(next(c for c in checks if c.label == "GPT identity").state, "STOP")

    def test_live_split_requires_confirmation_before_backup_or_write(self):
        stock = {"state": "stock", "userdata": {"first": "3568128", "last": "62275574"}, "disk_last": 62275574}
        with patch.object(installer.shutil, "which", return_value="/usr/bin/adb"), \
             patch.object(installer, "command", return_value=subprocess.CompletedProcess([], 0, "List of devices attached\nserial\trecovery\n", "")), \
             patch.object(installer, "twrp_identity", return_value=({"twrp": "3.7.1", "device": "gts9pwifi"}, [installer.Check("TWRP", "OK", "3.7.1")])), \
             patch.object(installer, "twrp_gpt_layout", return_value=(stock, [installer.Check("userdata layout", "OK", "stock")])), \
             patch.object(installer, "save_gpt_backup") as backup:
            checks, status = installer.run_split("serial", 50, write=True, confirm="")
        self.assertEqual(status, 2)
        self.assertEqual(next(c for c in checks if c.label == "Write confirmation").state, "STOP")
        backup.assert_not_called()

    def test_live_split_backs_up_first_and_writes_one_sgdisk_transaction(self):
        stock = {"state": "stock", "userdata": {"first": "3568128", "last": "62275574"}, "disk_last": 62275574}
        plan = installer.calculate_split(50)
        split_layout = {
            "state": "split",
            "userdata": {
                "name": "userdata", "type_guid": installer.X810_USERDATA_TYPE_GUID,
                "unique_guid": installer.X810_USERDATA_TYPE_GUID,
                "first": str(plan["userdata_first"]), "last": str(plan["userdata_last"]),
            },
            "linuxroot": {
                "name": "linuxroot", "type_guid": installer.X810_LINUX_TYPE_GUID,
                "unique_guid": "11111111-2222-3333-4444-555555555555",
                "first": str(plan["linuxroot_first"]), "last": str(plan["linuxroot_last"]),
            },
            "disk_last": installer.X810_USERDATA_END,
        }
        order = []
        commands = []
        updated_userdata_guid = str(uuid.uuid4()).upper()
        def fake_command(argv, timeout=15):
            commands.append(argv)
            if "devices" in argv:
                return subprocess.CompletedProcess(argv, 0, "List of devices attached\nserial\trecovery\n", "")
            if "--delete=34" in argv:
                order.append("write")
                return subprocess.CompletedProcess(argv, 0, "", "")
            if "--verify" in argv:
                return subprocess.CompletedProcess(argv, 0, "No problems found.\n", "")
            if "--info=34" in argv:
                return subprocess.CompletedProcess(argv, 0, f"""Partition GUID code: {installer.X810_USERDATA_TYPE_GUID} (Android data)
Partition unique GUID: {updated_userdata_guid}
First sector: {plan['userdata_first']}
Last sector: {plan['userdata_last']}
Partition name: 'userdata'
""", "")
            if "--info=35" in argv:
                return subprocess.CompletedProcess(argv, 0, f"""Partition GUID code: {installer.X810_LINUX_TYPE_GUID} (Linux filesystem)
Partition unique GUID: 11111111-2222-3333-4444-555555555555
First sector: {plan['linuxroot_first']}
Last sector: {plan['linuxroot_last']}
Partition name: 'linuxroot'
""", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        def fake_backup(*args, **kwargs):
            order.append("backup")
            return [installer.Check("GPT backup", "OK", "saved")]
        with patch.object(installer.shutil, "which", return_value="/usr/bin/adb"), \
             patch.object(installer, "command", side_effect=fake_command), \
             patch.object(installer, "twrp_identity", return_value=({"twrp": "3.7.1", "device": "gts9pwifi"}, [installer.Check("TWRP", "OK", "3.7.1")])), \
             patch.object(installer, "twrp_gpt_layout", side_effect=[
                 (stock, [installer.Check("layout", "OK", "stock")]),
                 (stock, [installer.Check("layout", "OK", "stock")]),
                 (split_layout, [installer.Check("layout", "OK", "split")]),
             ]), \
             patch.object(installer, "save_gpt_backup", side_effect=fake_backup):
            checks, status = installer.run_split(
                "serial", 50, write=True, confirm=installer.SPLIT_CONFIRMATION,
                backup_dir=Path("new-private-backup"),
            )
        self.assertEqual(status, 0)
        self.assertEqual(order, ["backup", "write"])
        write = next(argv for argv in commands if "--delete=34" in argv)
        self.assertEqual(write[-1], "/dev/block/sda")
        self.assertIn(f"--new=34:{plan['userdata_first']}:{plan['userdata_last']}", write)
        self.assertIn(f"--new=35:{plan['linuxroot_first']}:{plan['linuxroot_last']}", write)
        user_guid = next(part.split(":", 1)[1] for part in write if part.startswith("--partition-guid=34:"))
        self.assertNotEqual(user_guid, installer.X810_USERDATA_TYPE_GUID)
        self.assertRegex(user_guid, r"^[0-9A-Fa-f-]{36}$")
        self.assertFalse(any("reboot" in argv or "format" in argv for argv in commands))
        self.assertEqual(next(c for c in checks if c.label == "GPT write").state, "OK")

    @staticmethod
    def make_gpt_edges(disk_bytes):
        block = installer.GPT_CAPTURE_BLOCK
        total = disk_bytes // block
        array = bytes(128 * 128)
        array_crc = zlib.crc32(array) & 0xffffffff
        guid = uuid.uuid4().bytes_le
        first = bytearray(installer.GPT_CAPTURE_BYTES)
        last = bytearray(installer.GPT_CAPTURE_BYTES)
        first[2 * block:2 * block + len(array)] = array
        backup_entries_lba = total - 5
        last_array_offset = (backup_entries_lba - (total - installer.GPT_CAPTURE_BYTES // block)) * block
        last[last_array_offset:last_array_offset + len(array)] = array

        def header(current, backup, entries_lba):
            packed = bytearray(struct.pack(
                "<8sIIIIQQQQ16sQIII", b"EFI PART", 0x00010000, 92, 0, 0,
                current, backup, 34, total - 34, guid, entries_lba, 128, 128,
                array_crc,
            ))
            struct.pack_into("<I", packed, 16, zlib.crc32(packed) & 0xffffffff)
            return packed

        first[block:block + 92] = header(1, total - 1, 2)
        last[-block:-block + 92] = header(total - 1, 1, backup_entries_lba)
        return bytes(first), bytes(last)

    def test_gpt_backup_writes_only_host_files_and_validates_six_gpts(self):
        edges = {disk: self.make_gpt_edges(size) for disk, size in installer.X810_LUNS.items()}
        calls = []
        class Result:
            def __init__(self, argv, stdout="", stderr="", rc=0):
                self.args, self.stdout, self.stderr, self.returncode = argv, stdout, stderr, rc
        def fake_command(argv, timeout=15):
            calls.append(argv)
            if "--getsize64" in argv:
                disk = argv[-1].rsplit("/", 1)[-1]
                return Result(argv, str(installer.X810_LUNS[disk]))
            if "/queue/logical_block_size" in argv[-1]:
                return Result(argv, str(installer.GPT_CAPTURE_BLOCK))
            if "pull" in argv:
                Path(argv[-1]).write_bytes(b"sgdisk-test-backup")
                return Result(argv, "1 file pulled")
            if "--print" in argv:
                return Result(argv, "Partition table listing\n")
            if "--verify" in argv:
                return Result(argv, "No problems found.\n")
            return Result(argv)
        binary_outputs = []
        for disk in installer.X810_LUNS:
            binary_outputs.extend(edges[disk])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "x810-gpt"
            with patch.object(installer, "command", side_effect=fake_command), \
                 patch.object(installer, "adb_binary", side_effect=binary_outputs):
                checks = installer.save_gpt_backup("adb", "test-serial", out, {
                    "device": "gts9pwifi", "bootloader": "X810XXS5CYG1", "twrp": "3.7.1",
                })
            self.assertEqual(checks[0].state, "OK")
            self.assertTrue((out / "manifest.json").is_file())
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(set(manifest["luns"]), set(installer.X810_LUNS))
            self.assertIn("partition payloads are not included", manifest["scope"])
            self.assertEqual((out / "gpt/sda-sgdisk-verify.txt").read_text(), "No problems found.\n")
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all("reboot" not in call and "flash" not in call for call in calls))

    def test_gpt_backup_refuses_existing_output_without_device_io(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "exists"
            out.mkdir()
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                installer.save_gpt_backup("adb", "serial", out, {})

    def test_recovery_identity_uses_x810_recovery_property(self):
        values = {
            "ro.boot.em.model": "SM-X810",
            "ro.product.model": "SM-X816B",  # known generic TWRP property
            "ro.bootloader": "X810XXS5CYG1",
            "ro.product.device": "gts9pwifi",
            "ro.build.display.id": "TWRP",
            "ro.twrp.version": "3.7.1_12-0",
            "/dev/block/by-name/boot": str(installer.X810_BOOT_PARTITIONS["boot"]),
            "/dev/block/by-name/init_boot": str(installer.X810_BOOT_PARTITIONS["init_boot"]),
            "/dev/block/by-name/vendor_boot": str(installer.X810_BOOT_PARTITIONS["vendor_boot"]),
            "/dev/block/by-name/recovery": str(installer.X810_BOOT_PARTITIONS["recovery"]),
            "/dev/block/by-name/dtbo": str(installer.X810_BOOT_PARTITIONS["dtbo"]),
            "/dev/block/by-name/userdata": str(installer.X810_STOCK_USERDATA_BYTES),
            "test -b /dev/block/by-name/linuxroot && echo present || echo absent": "absent",
        }
        class Result:
            returncode = 0
            stderr = ""
            def __init__(self, stdout): self.stdout = stdout
        def fake_command(argv, timeout=15):
            return Result(values.get(argv[-1], "") + "\r\n")
        with patch.object(installer, "command", side_effect=fake_command):
            checks = installer.read_device("adb", "serial", "recovery")
        self.assertEqual(next(c for c in checks if c.label == "Tablet identity").state, "OK")
        self.assertEqual(next(c for c in checks if c.label == "Recovery").state, "OK")
        self.assertTrue(all(c.state == "OK" for c in checks))

    def test_recovery_partition_mismatch_stops_preflight(self):
        class Result:
            returncode = 0
            stderr = ""
            def __init__(self, stdout): self.stdout = stdout
        def fake_command(argv, timeout=15):
            name = argv[-1].rsplit("/", 1)[-1]
            if name == "boot": return Result("1\n")
            if name == "userdata": return Result(str(installer.X810_STOCK_USERDATA_BYTES))
            if "test -b" in argv[-1]: return Result("absent\n")
            return Result(str(installer.X810_BOOT_PARTITIONS.get(name, 0)))
        with patch.object(installer, "command", side_effect=fake_command):
            checks = installer.read_recovery_partitions("adb", "serial")
        self.assertEqual(next(c for c in checks if c.label == "partition boot").state, "STOP")
        self.assertEqual(next(c for c in checks if c.label == "userdata layout").state, "OK")

    def test_android_mode_never_claims_recovery_and_never_reboots(self):
        values = {"ro.boot.em.model": "SM-X810", "ro.product.device": "gts9pwifi", "ro.build.display.id": "One UI"}
        class Result:
            returncode = 0
            stderr = ""
            def __init__(self, stdout): self.stdout = stdout
        calls = []
        def fake_command(argv, timeout=15):
            calls.append(argv)
            return Result(values.get(argv[-1], "") + "\n")
        with patch.object(installer, "command", side_effect=fake_command):
            checks = installer.read_device("adb", "serial", "device")
        recovery = next(c for c in checks if c.label == "Recovery")
        self.assertEqual(recovery.state, "WAIT")
        self.assertIn("will not reboot", recovery.detail)
        self.assertTrue(all("reboot" not in call for call in calls))


if __name__ == "__main__":
    unittest.main()
