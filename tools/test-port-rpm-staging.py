#!/usr/bin/env python3
"""Guard against claiming Fedora-owned machine configuration in the port RPM."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SupportRpmStagingTests(unittest.TestCase):
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
        self.assertIn("--groups wheel,video", (ROOT / "tools/x810-install").read_text())

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
