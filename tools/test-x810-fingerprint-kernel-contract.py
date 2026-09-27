#!/usr/bin/env python3
"""Keep the X810 EL721 model metadata and kernel module packaging aligned."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FingerprintKernelContractTests(unittest.TestCase):
    def test_x810_fallback_uses_stock_sensor_model(self):
        driver = (ROOT / "kernel/files/egis_el721.c").read_text(encoding="utf-8")
        self.assertIn('#define EL721_DEFAULT_MODEL\t\t"X816"', driver)
        self.assertNotIn('#define EL721_DEFAULT_MODEL\t\t"X716"', driver)
        self.assertIn('"egistec,model", "etspi-modelinfo",', driver)
        self.assertIn("EL721_DEFAULT_MODEL, el721->model", driver)

    def test_reader_modules_are_built_and_requested(self):
        config = (ROOT / "kernel/files/config-gts9wifi.fragment").read_text(
            encoding="utf-8"
        )
        modules = (ROOT / "rootfs/overlay/usr/lib/modules-load.d/"
                   "gts9wifi-fingerprint.conf").read_text(encoding="utf-8")
        prepare = (ROOT / "kernel/prepare.sh").read_text(encoding="utf-8")
        spec = (ROOT / "kernel/kernel.spec").read_text(encoding="utf-8")

        self.assertIn("CONFIG_FINGERPRINT_EL721=m", config)
        self.assertIn("CONFIG_STAR_K250A_LEGO=m", config)
        self.assertIn("egis_el721", modules)
        self.assertIn("snvm", modules)
        self.assertIn('cp "$here/files/egis_el721.c" drivers/misc/', prepare)
        self.assertIn('cp -r "$here/files/snvm/." drivers/misc/snvm/', prepare)
        self.assertIn("modules_install dtbs_install", spec)
        self.assertIn("/usr/lib/modules/*", spec)


if __name__ == "__main__":
    unittest.main()
