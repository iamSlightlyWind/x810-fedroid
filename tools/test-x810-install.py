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
import os
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
            # systemd's escaped unit names can contain literal backslashes;
            # those are valid Linux filename bytes, not path separators.
            r"etc/systemd/system/dev-virtio\x2dports-org.qemu.guest_agent.0.device.wants": "valid escaped unit name\n",
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

    def test_no_update_does_not_accept_an_explicit_bundle_override(self):
        stderr = io.StringIO()
        with patch.object(sys, "argv", [str(SCRIPT), "install", "--no-update", "--bundle", "/tmp/ignored"]), \
             contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            installer.main()
        self.assertEqual(error.exception.code, 2)
        self.assertIn("selects the cached release automatically", stderr.getvalue())

    def test_no_update_uses_cached_bundle_without_querying_github(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_valid_bundle(Path(temporary) / "cached")
            with patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                            {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
                cached_bundle = installer.validate_install_bundle(root)
        with patch.object(sys, "argv", [str(SCRIPT), "install", "--no-update"]), \
             patch.object(sys, "stdin", TTY()), patch.object(sys, "stdout", TTY()), \
             patch.object(installer, "find_cached_install_bundle", return_value=cached_bundle), \
             patch.object(installer, "run_install", return_value=0) as run_install, \
             patch.object(installer, "latest_release", side_effect=AssertionError("local mode must not query GitHub")):
            self.assertEqual(installer.main(), 0)
        run_install.assert_called_once_with(cached_bundle, None, False, None)

    def test_no_update_stops_if_no_valid_cache_exists(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True

        stderr = io.StringIO()
        with patch.object(sys, "argv", [str(SCRIPT), "install", "--no-update"]), \
             patch.object(sys, "stdin", TTY()), patch.object(sys, "stdout", TTY()), \
             patch.object(installer, "find_cached_install_bundle", side_effect=ValueError("no complete cached X810 install release found")), \
             patch.object(installer, "run_install") as run_install, \
             patch.object(installer, "latest_release", side_effect=AssertionError("must not query GitHub")), \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(installer.main(), 2)
        run_install.assert_not_called()
        self.assertIn("--no-update found no valid cached install set", stderr.getvalue())

    def test_find_cached_install_bundle_picks_newest_complete_valid_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            old = self.make_valid_bundle(cache / "old-release")
            newest = self.make_valid_bundle(cache / "new-release")
            old_manifest = old / installer.CLEAN_MANIFEST
            new_manifest = newest / installer.CLEAN_MANIFEST
            os.utime(old_manifest, ns=(1_000_000_000, 1_000_000_000))
            os.utime(new_manifest, ns=(2_000_000_000, 2_000_000_000))
            (cache / "incomplete-release").mkdir()
            (cache / "incomplete-release" / "rootfs.tar.gz.part").write_bytes(b"partial")
            with patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                            {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
                selected = installer.find_cached_install_bundle(cache)
            self.assertEqual(selected.root, newest.resolve())

    def test_find_cached_install_bundle_fails_without_complete_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "no complete cached X810 install release"):
                installer.find_cached_install_bundle(Path(temporary))

    def test_release_check_distinguishes_compact_manifest_from_clean_installer(self):
        checks = installer.release_checks(None)
        self.assertEqual([check.state for check in checks], ["INFO", "BLOCKED"])
        self.assertIn("aggregate release from x810-fedora.yml", checks[0].detail)

        # The release manifest is provenance/checksum metadata, not the
        # Tab Companion port-update feed or a package that installs Fedora.
        release = {"tag_name": "test", "assets": [{"name": name} for name in (
            "manifest.json",
            "x810-fedora-port-1.2.3-1.aarch64.rpm", "rootfs.tar.gz",
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
            requests = []
            def opener(request, timeout=60):
                requests.append(request.full_url)
                return Response(payload_map[request.full_url])
            release = {"tag_name": "test-release", "assets": release_assets}
            cache = source / "downloaded"
            root = installer.download_release_install_set(release, cache, opener)
            bundle = installer.validate_install_bundle(root)
            self.assertEqual(bundle.manifest["source_commit"], "a" * 40)
            self.assertEqual(set(bundle.boot_images), {"boot", "init_boot", "vendor_boot", "dtbo"})
            self.assertEqual(bundle.kernel_rpm.read_bytes(), rpm.read_bytes())

            # Re-running for the same release should hash-check the cached payloads
            # and fetch only the small manifest, even with no room for another rootfs.
            requests.clear()
            low_disk = type("Usage", (), {"free": 128 * 1024**2})()
            with patch.object(installer.shutil, "disk_usage", return_value=low_disk):
                installer.download_release_install_set(release, cache, opener)
            self.assertEqual(requests, ["https://example.test/manifest.json"])

            # A corrupted cached payload is not trusted; it is replaced from the
            # same release after the missing-byte calculation passes.
            requests.clear()
            rootfs_cache = cache / "rootfs/rootfs.tar.gz"
            rootfs_cache.write_bytes(b"corrupt")
            enough_for_rootfs = type("Usage", (), {
                "free": len(payloads["rootfs.tar.gz"]) + 128 * 1024**2,
            })()
            with patch.object(installer.shutil, "disk_usage", return_value=enough_for_rootfs):
                installer.download_release_install_set(release, cache, opener)
            self.assertEqual(requests, ["https://example.test/manifest.json",
                                        "https://example.test/rootfs.tar.gz"])
            self.assertEqual(rootfs_cache.read_bytes(), payloads["rootfs.tar.gz"])

            # Rebuilt aggregate releases may have a new tag while retaining
            # byte-identical payloads. Reuse the prior release only by the
            # current manifest's size and SHA-256, not by filename alone.
            previous_rootfs = source / "releases/old-tag/rootfs/rootfs.tar.gz"
            previous_rootfs.parent.mkdir(parents=True)
            previous_rootfs.write_bytes(payloads["rootfs.tar.gz"])
            next_manifest = dict(release_manifest, release_tag="test-release-2")
            next_manifest_bytes = json.dumps(next_manifest).encode()
            next_payload_map = dict(payload_map)
            next_payload_map["https://example.test/manifest-2.json"] = next_manifest_bytes
            next_assets = [dict(asset) for asset in release_assets]
            next_manifest_asset = next(item for item in next_assets if item["name"] == "manifest.json")
            next_manifest_asset.update({"size": len(next_manifest_bytes),
                                        "browser_download_url": "https://example.test/manifest-2.json"})
            next_release = {"tag_name": "test-release-2", "assets": next_assets}
            requests.clear()
            next_cache = source / "releases/new-tag"
            installer.download_release_install_set(
                next_release, next_cache,
                lambda request, timeout=60: (requests.append(request.full_url),
                                              Response(next_payload_map[request.full_url]))[1],
            )
            self.assertNotIn("https://example.test/rootfs.tar.gz", requests)
            self.assertEqual(requests[0], "https://example.test/manifest-2.json")
            self.assertTrue((next_cache / "rootfs/rootfs.tar.gz").samefile(previous_rootfs))

    def test_release_asset_download_reports_progress_and_only_verifies_complete_digest(self):
        import io
        payload = b"download-progress-test"
        events = []
        with tempfile.TemporaryDirectory() as temp:
            asset = {"browser_download_url": "https://example.test/test.bin", "size": len(payload)}
            record = {"name": "test.bin", "size_bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()}

            def opener(request, timeout=60):
                self.assertEqual(timeout, 60)
                return io.BytesIO(payload)

            result = installer._download_checked_release_asset(
                asset, record, Path(temp) / "test.bin", opener,
                progress=lambda name, done, total: events.append((name, done, total)),
            )
            self.assertEqual(result.read_bytes(), payload)
            self.assertEqual(events[0], ("test.bin", 0, len(payload)))
            self.assertEqual(events[-1], ("test.bin", len(payload), len(payload)))

    def test_interrupted_asset_download_leaves_no_partial_file(self):
        payload = b"verified-prefix-then-interrupted"
        class InterruptedResponse:
            def __init__(self): self.reads = 0
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size):
                self.reads += 1
                if self.reads == 1:
                    return payload[:10]
                raise KeyboardInterrupt
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "rootfs/rootfs.tar.gz"
            asset = {"browser_download_url": "https://example.test/rootfs.tar.gz", "size": len(payload)}
            record = {"name": "rootfs.tar.gz", "size_bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()}
            with self.assertRaises(KeyboardInterrupt):
                installer._download_checked_release_asset(
                    asset, record, destination,
                    opener=lambda _request, timeout=60: InterruptedResponse(),
                )
            self.assertFalse(destination.exists())
            partial = destination.with_name(destination.name + ".part")
            self.assertEqual(partial.read_bytes(), payload[:10])

    def test_release_asset_download_resumes_range_and_verifies_full_digest(self):
        import io
        payload = b"existing-prefix-and-ranged-suffix"
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "rootfs/rootfs.tar.gz"
            destination.parent.mkdir()
            partial = destination.with_name(destination.name + ".part")
            partial.write_bytes(payload[:12])
            asset = {"browser_download_url": "https://example.test/rootfs.tar.gz", "size": len(payload)}
            record = {"name": "rootfs.tar.gz", "size_bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()}
            class RangeResponse(io.BytesIO):
                status = 206
                headers = {"Content-Range": f"bytes 12-{len(payload) - 1}/{len(payload)}"}
            def opener(request, timeout=60):
                self.assertEqual(request.get_header("Range"), "bytes=12-")
                return RangeResponse(payload[12:])
            result = installer._download_checked_release_asset(asset, record, destination, opener)
            self.assertEqual(result.read_bytes(), payload)
            self.assertFalse(partial.exists())

    def test_release_status_tolerates_malformed_asset_metadata(self):
        checks = installer.release_checks({"assets": [None, {"name": []}], "tag_name": "odd"})
        self.assertEqual([check.state for check in checks], ["INFO", "BLOCKED"])

    def test_clean_bundle_manifest_and_build_pair_validate(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(installer.INSTALL_BOOT_IMAGE_SIZES,
                                                              {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            root = self.make_valid_bundle(Path(temp) / "bundle")
            bundle = installer.validate_install_bundle(root)
            self.assertEqual(bundle.manifest["device"]["model"], "SM-X810")
            with tarfile.open(bundle.rootfs_archive, "r:gz") as archive:
                self.assertIn(
                    r"etc/systemd/system/dev-virtio\x2dports-org.qemu.guest_agent.0.device.wants",
                    {member.name for member in archive.getmembers()},
                )
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
        calls = []
        required = ("sgdisk", "blockdev", "dd", "gzip", "tar", "mke2fs", "mount",
                    "umount", "sync", "chroot", "sh", "test")
        class FakeClient:
            def shell(self, *args, timeout=30):
                calls.append(args)
                if args == ("command", "-v", "mke2fs"):
                    return subprocess.CompletedProcess(args, 127, "", "not found")
                return subprocess.CompletedProcess(args, 0, f"/sbin/{args[-1]}\n", "")
        checks = installer.preflight_twrp_tools(FakeClient())
        self.assertEqual(checks[0].state, "STOP")
        self.assertIn("mke2fs", checks[0].detail)
        self.assertEqual(calls, [("command", "-v", utility) for utility in required] + [
            ("command", "-v", "blkid"),
        ])
        self.assertTrue(all(args[:2] != ("sh", "-c") for args in calls))

    def test_twrp_utility_preflight_accepts_toybox_blkid_applet(self):
        calls = []
        required = ("sgdisk", "blockdev", "dd", "gzip", "tar", "mke2fs", "mount",
                    "umount", "sync", "chroot", "sh", "test")
        class FakeClient:
            def shell(self, *args, timeout=30):
                calls.append(args)
                if args == ("command", "-v", "blkid"):
                    return subprocess.CompletedProcess(args, 127, "", "not found")
                if args == ("toybox", "blkid", "--help"):
                    return subprocess.CompletedProcess(args, 0, "usage: blkid [-s TAG] DEV...\n", "")
                return subprocess.CompletedProcess(args, 0, f"/system/bin/{args[-1]}\n", "")
        checks = installer.preflight_twrp_tools(FakeClient())
        self.assertEqual(checks, [installer.Check(
            "TWRP utilities", "OK", "all required recovery commands are available")
        ])
        self.assertEqual(calls, [("command", "-v", utility) for utility in required] + [
            ("command", "-v", "blkid"), ("command", "-v", "toybox"),
            ("toybox", "blkid", "--help"),
        ])

    def test_userdata_filesystem_type_supports_toybox_output(self):
        class FakeClient:
            def shell(self, *args, timeout=30):
                if args[0] == "blkid":
                    return subprocess.CompletedProcess(args, 127, "", "not found")
                if args == ("toybox", "blkid", "-s", "TYPE", "/dev/block/by-name/userdata"):
                    return subprocess.CompletedProcess(args, 0,
                        '/dev/block/by-name/userdata: TYPE="f2fs"\n', "")
                raise AssertionError(f"unexpected command: {args}")
        self.assertEqual(installer.twrp_userdata_filesystem_type(FakeClient()), "f2fs")

    def test_twrp_utility_preflight_accepts_all_individually_resolved_commands(self):
        calls = []
        class FakeClient:
            def shell(self, *args, timeout=30):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, f"/sbin/{args[-1]}\n", "")
        checks = installer.preflight_twrp_tools(FakeClient())
        self.assertEqual(checks, [installer.Check(
            "TWRP utilities", "OK", "all required recovery commands are available")
        ])
        self.assertTrue(calls)
        self.assertTrue(all(len(args) == 3 and args[:2] == ("command", "-v") for args in calls))

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
                 patch.object(installer, "adb_target", side_effect=[("serial", "device"), ("serial", "device"), ("serial", "recovery")]), \
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
                 patch.object(installer, "android_preflight") as android_preflight, \
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
            android_preflight.assert_not_called()
            self.assertEqual(status, 0)

    def test_fresh_install_stops_before_release_download_when_android_preflight_fails(self):
        output = io.StringIO()
        failed = [installer.Check("Android root", "STOP", "su unavailable")]
        with patch.object(installer.platform, "system", return_value="Linux"), \
             patch.object(installer.shutil, "which", return_value="/usr/bin/adb"), \
             patch.object(installer, "adb_target", return_value=("serial", "device")), \
             patch.object(installer, "android_preflight", return_value=("serial", failed)), \
             patch.object(installer, "open_install_bundle") as open_bundle, \
             contextlib.redirect_stdout(output):
            status = installer.run_install(None)
        self.assertEqual(status, 2)
        open_bundle.assert_not_called()
        self.assertIn("Android root", output.getvalue())
        self.assertIn("installer made no tablet changes", output.getvalue())

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
            image_pushes = {path: data for path, data in pushed.items() if path.endswith(".img")}
            self.assertEqual(len(image_pushes), 8)
            self.assertEqual(pushed["/tmp/x810-linuxroot/var/lib/x810-boot-sets/android/name.txt"], b"Android\n")
            self.assertEqual(pushed["/tmp/x810-linuxroot/var/lib/x810-boot-sets/fedora/name.txt"], b"Fedora\n")
            self.assertEqual(len(pushed), 10)
            for path, data in image_pushes.items():
                self.assertEqual(len(data), 8)
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

    def test_install_password_has_no_minimum_length_requirement(self):
        text_answers = iter(["Alice Example", "alice", "tablet"])
        password_answers = iter(["42", "42"])
        with patch.object(installer, "hash_password", return_value="$6$short-pass") as hash_password:
            details = installer.prompt_install_details(
                100 * 1024**3, 32 * 1024**3,
                password_reader=lambda _prompt: next(password_answers),
                input_func=lambda _prompt: next(text_answers),
            )
        self.assertEqual(details["password_hash"], "$6$short-pass")
        hash_password.assert_called_once_with("42")

    def test_boot_set_label_uses_push_not_flattened_printf_shell(self):
        class FakeClient:
            def __init__(self):
                self.files = {}
                self.shell_calls = []
            def shell(self, *args, **kwargs):
                self.shell_calls.append(args)
                return subprocess.CompletedProcess([], 0, "", "")
            def push(self, source, remote_path, **kwargs):
                self.files[remote_path] = source.read_bytes()
                return subprocess.CompletedProcess([], 0, "", "")
            def read_binary(self, remote_command, **kwargs):
                return self.files[remote_command.removeprefix("cat ")]

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
                installer.INSTALL_BOOT_IMAGE_SIZES,
                {name: 8 for name in ("boot", "init_boot", "vendor_boot", "dtbo")}):
            image_dir = Path(temporary) / "images"
            image_dir.mkdir()
            images = {}
            for name in installer.INSTALL_BOOT_IMAGE_SIZES:
                image = image_dir / f"{name}.img"
                image.write_bytes((name.encode() + b"_")[:8].ljust(8, b"x"))
                images[name] = image
            client = FakeClient()
            installer.stage_boot_set_files(client, "/tmp/root", "android", "Android", images)

        self.assertEqual(client.files["/tmp/root/var/lib/x810-boot-sets/android/name.txt"], b"Android\n")
        self.assertIn(("chmod", "0644", "/tmp/root/var/lib/x810-boot-sets/android/name.txt"), client.shell_calls)
        self.assertFalse(any(args[:2] == ("sh", "-c") for args in client.shell_calls))
        self.assertFalse(any("printf" in repr(args) for args in client.shell_calls))

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
            "ro.boot.flash.locked": "0",
            "ro.boot.vbmeta.device_state": "unlocked",
            "ro.boot.verifiedbootstate": "orange",
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
