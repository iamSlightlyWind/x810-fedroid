#!/usr/bin/env python3
"""Check the X810 GPIO-powered vibrator wiring and kernel integration."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VibratorKernelContractTests(unittest.TestCase):
    def test_gpio_vibrator_accepts_x810_without_separate_regulator(self):
        config = (ROOT / "kernel/files/config-gts9wifi.fragment").read_text(
            encoding="utf-8"
        )
        dts = (ROOT / "kernel/files/sm8550-samsung-gts9wifi.dts").read_text(
            encoding="utf-8"
        )
        prepare = (ROOT / "kernel/prepare.sh").read_text(encoding="utf-8")
        driver_patch = (ROOT / "kernel/patches/gpio-vibrator-optional-vcc.patch").read_text(
            encoding="utf-8"
        )
        spec = (ROOT / "kernel/kernel.spec").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/x810-fedora.yml").read_text(
            encoding="utf-8"
        )

        vibrator = re.search(r"(?ms)^\s*vibrator\s*\{([^}]*)\}", dts)
        self.assertIsNotNone(vibrator)
        self.assertIn('compatible = "gpio-vibrator";', vibrator.group(1))
        self.assertIn(
            "enable-gpios = <&tlmm 18 GPIO_ACTIVE_HIGH>;", vibrator.group(1)
        )
        self.assertNotIn("vcc-supply", vibrator.group(1))
        self.assertIn("CONFIG_INPUT_GPIO_VIBRA=y", config)

        # kernel.spec runs fdtget against the installed DTB. Fedora's `dtc`
        # package provides /usr/bin/fdtget, and the kernel build container
        # installs that package; keep the RPM build dependency explicit too.
        self.assertIn("BuildRequires:  dtc", spec)
        self.assertIn("BuildRequires:  python3", spec)
        self.assertIn('["fdtget", *command]', spec)
        self.assertIn("python3 tools/test-x810-vibrator-kernel-contract.py", workflow)

        # prepare.sh applies the patch before building. Explicit regulator
        # errors remain fatal; only a device with no vcc-supply may omit it.
        self.assertIn('for p in "$here"/patches/*.patch', prepare)
        self.assertIn("devm_regulator_get_optional", driver_patch)
        self.assertIn("PTR_ERR(vibrator->vcc) == -ENODEV &&", driver_patch)
        self.assertIn(
            '!device_property_present(&pdev->dev, "vcc-supply")', driver_patch
        )
        self.assertIn("vibrator->vcc && !vibrator->vcc_on", driver_patch)
        self.assertIn("vibrator->vcc && vibrator->vcc_on", driver_patch)

        # The RPM validates the compiled DTB's actual TLMM reference/line and
        # required kernel options, not only the board source text.
        for option in (
            "CONFIG_INPUT_GPIO_VIBRA=y",
            "CONFIG_CPU_FREQ_GOV_SCHEDUTIL=y",
            "CONFIG_CPU_FREQ_GOV_PERFORMANCE=y",
        ):
            self.assertIn(option, spec)
        self.assertIn("sm8550-samsung-gts9wifi.dtb", spec)
        self.assertIn('prop("/__symbols__", "tlmm")', spec)
        self.assertIn("gpio_line != 18 or gpio_flags != 0", spec)
        self.assertIn('"qcom,sm8550-tlmm"', spec)


if __name__ == "__main__":
    unittest.main()
