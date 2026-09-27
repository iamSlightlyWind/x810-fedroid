#!/usr/bin/env python3
"""Guard against claiming Fedora-owned machine configuration in the port RPM."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SupportRpmStagingTests(unittest.TestCase):
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

    def test_port_update_repairs_uid_1000_camera_access(self):
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertIn("desktop_user=$(getent passwd 1000", spec)
        self.assertIn('usermod -a -G video "$desktop_user"', spec)
        self.assertIn('getent group input', spec)
        self.assertIn('usermod -a -G input "$desktop_user"', spec)
        install = (ROOT / "tools/x810-install").read_text()
        self.assertIn("--groups wheel,video,input", install)

    def test_adsp_autostart_is_disabled_in_image_and_on_package_update(self):
        builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        enabled_units = builder.split("for unit in \\\n", 1)[1].split("\ndo\n", 1)[0]
        self.assertNotIn("gts9wifi-adsp-boot", enabled_units)
        self.assertIn(
            "disable gts9wifi-adsp-boot.service",
            builder,
        )

        preset = (ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/"
                  "85-gts9wifi.preset").read_text(encoding="utf-8")
        self.assertIn("disable gts9wifi-adsp-boot.service", preset)
        self.assertIn("disable hexagonrpcd-adsp-sensorspd.service", preset)

        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertIn("systemctl disable --quiet gts9wifi-adsp-boot.service", spec)
        self.assertIn("hexagonrpcd-adsp-sensorspd.service", spec)

    def test_gnome_power_profiles_use_tuned_ppd(self):
        builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
        self.assertIn("install tuned-ppd", builder)
        self.assertIn("tuned.service tuned-ppd.service", builder)

        preset = (ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/"
                  "85-gts9wifi.preset").read_text(encoding="utf-8")
        self.assertIn("enable tuned.service", preset)
        self.assertIn("enable tuned-ppd.service", preset)

        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertIn("Requires:       tuned-ppd", spec)
        self.assertIn("systemctl start tuned-ppd.service", spec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
