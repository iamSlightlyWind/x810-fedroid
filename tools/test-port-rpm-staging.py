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


if __name__ == "__main__":
    unittest.main(verbosity=2)
