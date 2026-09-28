#!/usr/bin/env python3
"""Guard against claiming Fedora-owned machine configuration in the port RPM."""

from pathlib import Path
import re
import runpy
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SupportRpmStagingTests(unittest.TestCase):
    def test_measured_x810_touch_calibration_is_in_fresh_image_and_update(self):
        rule_path = (
            ROOT
            / "rootfs/overlay/usr/lib/udev/rules.d/72-gts9wifi-touch-calibration.rules"
        )
        self.assertTrue(rule_path.is_file())
        rule = rule_path.read_text(encoding="utf-8")
        self.assertIn('ATTRS{name}=="FTS1BA90A Touchscreen"', rule)
        self.assertIn(
            'ENV{LIBINPUT_CALIBRATION_MATRIX}="0.94655 0 -0.02330 0 0.94728 0.06605"',
            rule,
        )

        # Both install paths stage the complete rootfs overlay, so this one
        # measured device rule reaches clean installs and support-RPM updates.
        for builder in ("rootfs/build-rootfs.sh", "tools/build-port-support-rpm.sh"):
            self.assertIn(
                'cp -a "$repo_dir/rootfs/overlay/."',
                (ROOT / builder).read_text(encoding="utf-8"),
            )

    def test_ppd_profile_map_targets_a_real_tuned_performance_profile(self):
        contract = runpy.run_path(str(ROOT / "tools/test-port-build-contract.py"))
        check_mapping = contract["check_power_profile_mapping"]
        with tempfile.TemporaryDirectory() as temp:
            rootfs = Path(temp)
            ppd = rootfs / "etc/tuned/ppd.conf"
            profile = rootfs / "usr/lib/tuned/profiles/throughput-performance/tuned.conf"
            ppd.parent.mkdir(parents=True)
            profile.parent.mkdir(parents=True)
            ppd.write_text(
                "[main]\ndefault=balanced\n\n"
                "[profiles]\npower-saver=powersave\nbalanced=balanced\n"
                "performance=throughput-performance\n",
                encoding="utf-8",
            )
            profile.write_text(
                "[cpu]\ngovernor=performance\nmin_perf_pct=100\n",
                encoding="utf-8",
            )
            check_mapping(rootfs)

            # A syntactically present mapping is not enough: the selected
            # profile must actually request the performance governor.
            profile.write_text(
                "[cpu]\ngovernor=schedutil\nmin_perf_pct=100\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                check_mapping(rootfs)

    def test_hi1337_tuning_flows_to_fresh_image_and_support_rpm(self):
        tuning = ROOT / (
            "rootfs/overlay/usr/share/libcamera/ipa/simple/hi1337-gts9u.yaml"
        )
        self.assertTrue(tuning.is_file())
        contents = tuning.read_text(encoding="utf-8")
        self.assertIn("SPDX-License-Identifier: CC0-1.0", contents)
        for algorithm in ("BlackLevel:", "Awb:", "Ccm:", "Agc:"):
            self.assertIn(algorithm, contents)

        rootfs_builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        support_builder = (ROOT / "tools/build-port-support-rpm.sh").read_text(
            encoding="utf-8"
        )
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        contract = (ROOT / "tools/test-port-build-contract.py").read_text(
            encoding="utf-8"
        )
        # Both build paths stage the full overlay. The built-image contract
        # verifies the actual RPM file list, its payload digest, and the file
        # installed in the fresh rootfs.
        self.assertIn('cp -a "$repo_dir/rootfs/overlay/." "$rootfs/"', rootfs_builder)
        self.assertIn('cp -a "$repo_dir/rootfs/overlay/." "$stage/"', support_builder)
        self.assertIn(
            "License:        CC0-1.0 AND MIT AND LGPL-2.1-or-later AND BSD-2-Clause",
            spec,
        )
        self.assertIn('"/usr/share/libcamera/ipa/simple/hi1337-gts9u.yaml"', contract)
        self.assertIn("fresh rootfs is missing the HI1337 IPA tuning YAML", contract)
        self.assertIn(
            'HI1337_TUNING_FILE = "/usr/share/libcamera/ipa/simple/hi1337-gts9u.yaml"',
            contract,
        )
        self.assertIn(
            "support RPM {filename} payload differs from the fresh rootfs", contract
        )

    def test_rootfs_build_prunes_both_support_rpm_arches(self):
        builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        command = re.search(
            r'find "\$outdir" -maxdepth 1 -type f \\\n'
            r'\s+\\\( -name \'x810-fedora-port-\*\.noarch\.rpm\' \\\n'
            r'\s+-o -name \'x810-fedora-port-\*\.aarch64\.rpm\' \\\) -delete',
            builder,
        )
        self.assertIsNotNone(command)

        with tempfile.TemporaryDirectory(prefix="x810-rpm-prune-") as temp:
            outdir = Path(temp)
            stale = [
                outdir / "x810-fedora-port-0.1-1.noarch.rpm",
                outdir / "x810-fedora-port-0.1-2.aarch64.rpm",
            ]
            keep = outdir / "unrelated.rpm"
            for path in stale + [keep]:
                path.touch()
            subprocess.run(
                ["bash", "-euc", f"outdir={str(outdir)!r}; {command.group(0)}"],
                check=True,
            )
            self.assertFalse(any(path.exists() for path in stale))
            self.assertTrue(keep.exists())

    def test_sensor_recovery_has_one_global_start_deadline(self):
        helper = (ROOT / "rootfs/overlay/usr/libexec/gts9wifi-sensors-resume").read_text(
            encoding="utf-8"
        )
        unit = (ROOT / "rootfs/overlay/usr/lib/systemd/system/"
                "gts9wifi-wait-sensor-proxy.service").read_text(encoding="utf-8")

        runtime_match = re.search(r"^MAX_RUNTIME_SEC=(\d+)$", helper, re.MULTILINE)
        timeout_match = re.search(r"^TimeoutStartSec=(\d+)$", unit, re.MULTILINE)
        self.assertIsNotNone(runtime_match)
        self.assertIsNotNone(timeout_match)
        runtime_budget = int(runtime_match.group(1))
        unit_timeout = int(timeout_match.group(1))

        # The helper's budget is shared across every phase and leaves ample room
        # below systemd's unit timeout, including the two-second ExecStartPre.
        self.assertLessEqual(runtime_budget, 180)
        self.assertLess(runtime_budget + 2, unit_timeout)
        self.assertIn("deadline=$((started_at + MAX_RUNTIME_SEC))", helper)
        self.assertIn("read -r uptime_seconds _ < /proc/uptime", helper)
        self.assertIn("remaining=$((deadline - now))", helper)

        # Every potentially blocking systemd/SSC operation must share the same
        # run_bounded() deadline; per-probe timeout alone was not sufficient.
        self.assertIn("jobs=$(run_bounded systemctl list-jobs", helper)
        self.assertIn(
            "if run_bounded systemctl is-active --quiet hexagonrpcd-adsp-sensorspd.service",
            helper,
        )
        self.assertIn(
            'if [ "$attempt" -eq 5 ] && [ "$restarted_sensorspd" -eq 0 ]', helper
        )
        self.assertIn(
            "run_bounded systemctl restart hexagonrpcd-adsp-sensorspd.service", helper
        )
        self.assertIn("output=$(run_bounded timeout 4 ssccli", helper)
        self.assertIn("run_bounded systemctl restart iio-sensor-proxy.service", helper)
        self.assertIn("run_bounded sleep 1", helper)
        self.assertIn("run_bounded sleep 2", helper)
        self.assertNotIn("$(date +%s)", helper)

    def test_systemd_owned_configs_remain_image_local(self):
        builder = (ROOT / "tools/build-port-support-rpm.sh").read_text(encoding="utf-8")
        self.assertIn(
            'rm -f "$stage/etc/machine-info" "$stage/etc/locale.conf"', builder
        )
        self.assertFalse((ROOT / "rootfs/overlay/etc/locale.conf").exists())
        self.assertTrue((ROOT / "rootfs/overlay/etc/machine-info").is_file())

    def test_unverified_x810_suspend_targets_are_masked_in_image_and_update(self):
        overlay = ROOT / "rootfs/overlay"
        for target in (
            "sleep.target",
            "suspend.target",
            "hibernate.target",
            "hybrid-sleep.target",
            "suspend-then-hibernate.target",
        ):
            mask = overlay / "etc/systemd/system" / target
            self.assertTrue(mask.is_symlink(), f"missing systemd mask: {mask}")
            self.assertEqual(mask.readlink(), Path("/dev/null"))

        logind = (
            overlay / "etc/systemd/logind.conf.d/10-gts9wifi-lid.conf"
        ).read_text(encoding="utf-8")
        for key in (
            "HandleLidSwitch",
            "HandleLidSwitchExternalPower",
            "HandleLidSwitchDocked",
        ):
            self.assertRegex(logind, rf"(?m)^{key}=ignore$")

        # Fresh installs and support-RPM upgrades both consume the overlay;
        # the RPM post-transaction hook reloads systemd unit definitions.
        for builder in (
            ROOT / "rootfs/build-rootfs.sh",
            ROOT / "tools/build-port-support-rpm.sh",
        ):
            self.assertIn(
                'cp -a "$repo_dir/rootfs/overlay/."',
                builder.read_text(encoding="utf-8"),
            )
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertIn("systemctl daemon-reload", spec)
        self.assertIn("systemctl kill --signal=HUP systemd-logind.service", spec)

    def test_pipewire_audio_override_is_system_wide_and_keeps_libcamera(self):
        config = ROOT / (
            "rootfs/overlay/usr/share/wireplumber/wireplumber.conf.d/"
            "90-x810-audio-routing.conf"
        )
        text = config.read_text(encoding="utf-8")
        self.assertIn("wireplumber.profiles", text)
        self.assertIn("main = {", text)
        self.assertIn("monitor.v4l2 = disabled", text)
        self.assertNotIn("monitor.libcamera = disabled", text)
        self.assertIn('device.name = "alsa_card.comprC0"', text)
        self.assertIn("device.disabled = true", text)
        self.assertFalse(
            (ROOT / "rootfs/overlay/usr/lib/systemd/user/wireplumber.service.d").exists()
        )

    def test_gnome_image_and_support_rpm_require_the_pipewire_runtime(self):
        builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        # This build globally disables weak dependencies. The Workstation
        # multimedia group contains clients/session policy, but the daemon is
        # only recommended by some of those packages, so force it explicitly.
        install = re.search(
            r"(?ms)^if \[ \"\$desktop\" = \"gnome\" \]; then\n"
            r".*?^    dnf .*? --setopt=install_weak_deps=False .*? install \\\n"
            r"(?P<packages>.*?)(?=^    # The first-login welcome wizard)",
            builder,
        )
        self.assertIsNotNone(install)
        packages = install.group("packages")
        for package in (
            "pipewire",
            "wireplumber",
            "pipewire-pulseaudio",
            "pipewire-alsa",
        ):
            self.assertRegex(
                packages,
                rf"(?<![A-Za-z0-9_-]){re.escape(package)}(?![A-Za-z0-9_-])",
            )
            self.assertRegex(spec, rf"(?m)^Requires:\s+{re.escape(package)}\s*$")

    def test_port_update_repairs_uid_1000_camera_access(self):
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertIn("%global debug_package %{nil}", spec)
        self.assertIn("desktop_user=$(getent passwd 1000", spec)
        self.assertIn('usermod -a -G video "$desktop_user"', spec)
        self.assertIn('getent group input', spec)
        self.assertIn('usermod -a -G input "$desktop_user"', spec)
        install = (ROOT / "tools/x810-install").read_text()
        self.assertIn("--groups wheel,video,input", install)

    def test_support_rpm_builder_keeps_stdout_to_single_path(self):
        builder = (ROOT / "tools/build-port-support-rpm.sh").read_text(encoding="utf-8")
        self.assertIn(
            'bash "$repo_dir/tools/build-libcamera-hi1337-ipa.sh" "$stage" >&2',
            builder,
        )

    def test_rpm_update_removes_only_legacy_vendor_mount_mask(self):
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        match = re.search(
            r'(?ms)^    vendor_mount_mask=/etc/systemd/system/vendor\.mount\n'
            r'    if .*?^    fi$',
            spec,
        )
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory(prefix="x810-vendor-mask-") as temp:
            root = Path(temp)
            unit = root / "vendor.mount"
            snippet = match.group(0).replace(
                "/etc/systemd/system/vendor.mount", str(unit)
            )

            unit.symlink_to("/dev/null")
            subprocess.run(["bash", "-euc", snippet], check=True)
            self.assertFalse(unit.is_symlink())

            unit.write_text("local override\n", encoding="utf-8")
            subprocess.run(["bash", "-euc", snippet], check=True)
            self.assertEqual(unit.read_text(encoding="utf-8"), "local override\n")
            unit.unlink()

            unit.symlink_to("/tmp/vendor.mount")
            subprocess.run(["bash", "-euc", snippet], check=True)
            self.assertTrue(unit.is_symlink())
            self.assertEqual(unit.readlink(), Path("/tmp/vendor.mount"))

    def test_gnome_power_profiles_use_tuned_ppd(self):
        builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        self.assertIn("install tuned-ppd", builder)
        enabled_units = builder.split("for unit in \\\n", 1)[1].split("\ndo\n", 1)[0]
        self.assertIn("tuned.service tuned-ppd.service", enabled_units)

        preset = (ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/"
                  "85-gts9wifi.preset").read_text(encoding="utf-8")
        self.assertIn("enable tuned.service", preset)
        self.assertIn("enable tuned-ppd.service", preset)

        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertIn("Requires:       tuned-ppd", spec)
        self.assertIn("systemctl start tuned-ppd.service", spec)

        build_contract = (ROOT / "tools/test-port-build-contract.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("check_power_profile_mapping(rootfs)", build_contract)
        self.assertIn('"performance": "throughput-performance"', build_contract)
        self.assertIn('"balanced": "balanced"', build_contract)
        self.assertIn('"power-saver": "powersave"', build_contract)
        self.assertIn('"governor", fallback="") != "performance"', build_contract)
        self.assertIn('"min_perf_pct", fallback="") != "100"', build_contract)

        kernel_config = (ROOT / "kernel/files/config-mainline.aarch64").read_text(
            encoding="utf-8"
        )
        for governor in (
            "CONFIG_CPU_FREQ_GOV_SCHEDUTIL=y",
            "CONFIG_CPU_FREQ_GOV_PERFORMANCE=y",
        ):
            self.assertIn(governor, kernel_config)

    def test_sensor_startup_stages_the_exact_x810_registry_before_hexagonrpcd(self):
        builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        enabled_units = builder.split("for unit in \\\n", 1)[1].split("\ndo\n", 1)[0]
        self.assertNotIn("gts9wifi-adsp-boot", enabled_units)
        self.assertIn("gts9wifi-wait-sensor-proxy", enabled_units)
        self.assertIn("gts9wifi-sensor-registry-perms", enabled_units)

        cache_fix = (ROOT / "rootfs/overlay/usr/libexec/"
                     "gts9wifi-sensor-registry-perms").read_text(encoding="utf-8")
        self.assertIn('VENDOR_SENSOR = Path("/vendor/etc/sensors")', cache_fix)
        self.assertIn('PERSIST_SENSOR = Path("/mnt/vendor/persist/sensors/registry")', cache_fix)
        self.assertIn('PERSIST_OUTPUT_PATH = "/mnt/vendor/persist/sensors/registry/registry"',
                      cache_fix)
        self.assertIn("shutil.copy2", cache_fix)
        self.assertIn("cached_mtime != int(path.stat().st_mtime)", cache_fix)
        self.assertIn("os.chown(entry, uid, gid", cache_fix)
        self.assertIn('"sensors_registry"', cache_fix)
        self.assertNotIn("touch -h -d @0", cache_fix)
        self.assertNotIn("chmod -R a+rwX", cache_fix)

        builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        self.assertIn('sensor_tree="$rootfs/usr/share/qcom/sm8550/Samsung/gts9wifi"', builder)
        self.assertIn('rm -rf -- "$sensor_tree/sensors" "$sensor_tree/socinfo"', builder)
        self.assertNotIn('find "$sensor_tree" -exec touch -h -d @0 {} +', builder)

        sensorspd = (ROOT / "rootfs/overlay/etc/systemd/system/"
                     "hexagonrpcd-adsp-sensorspd.service.d/"
                     "10-gts9wifi-hexagonfs.conf").read_text(encoding="utf-8")
        self.assertIn("Requires=gts9wifi-adsp-boot.service", sensorspd)
        self.assertIn("gts9wifi-sensor-registry-perms.service", sensorspd)
        self.assertIn("After=gts9wifi-adsp-boot.service", sensorspd)
        self.assertIn("-R /run/gts9wifi-hexagonfs", sensorspd)

        registry_service = (ROOT / "rootfs/overlay/usr/lib/systemd/system/"
                            "gts9wifi-sensor-registry-perms.service").read_text(
                                encoding="utf-8")
        self.assertIn("RequiresMountsFor=/vendor /mnt/vendor/persist", registry_service)

        wait_proxy = (ROOT / "rootfs/overlay/usr/lib/systemd/system/"
                      "gts9wifi-wait-sensor-proxy.service").read_text(encoding="utf-8")
        self.assertIn("Requires=gts9wifi-panel-coldboot-recover.service", wait_proxy)
        self.assertIn("Wants=hexagonrpcd-adsp-sensorspd.service", wait_proxy)
        self.assertIn("After=gts9wifi-panel-coldboot-recover.service", wait_proxy)
        self.assertIn("After=hexagonrpcd-adsp-sensorspd.service", wait_proxy)
        self.assertNotIn("Before=display-manager.service", wait_proxy)

        preset = (ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/"
                  "85-gts9wifi.preset").read_text(encoding="utf-8")
        self.assertIn("disable gts9wifi-adsp-boot.service", preset)
        self.assertIn("disable hexagonrpcd-adsp-sensorspd.service", preset)
        self.assertIn("enable gts9wifi-sensor-registry-perms.service", preset)

        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertIn("systemctl enable gts9wifi-sensor-registry-perms.service", spec)
        self.assertIn("systemctl disable --quiet gts9wifi-adsp-boot.service", spec)
        self.assertIn("hexagonrpcd-adsp-sensorspd.service", spec)
        self.assertNotIn("restart gts9wifi-sensor-registry-perms.service", spec)
        self.assertIn("TimeoutStartSec=20s", spec)
        self.assertIn("cmp -s - \"$sensor_timeout_override\"", spec)

        wait_node = (ROOT / "rootfs/overlay/usr/libexec/"
                     "gts9wifi-wait-fastrpc").read_text(encoding="utf-8")
        self.assertIn("udevadm settle", wait_node)
        self.assertIn("fastrpc:fastrpc", wait_node)


if __name__ == "__main__":
    unittest.main(verbosity=2)
