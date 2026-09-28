#!/usr/bin/env python3
"""Keep the X810 EL721 model metadata and kernel module packaging aligned."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def c_function_body(source: str, name: str) -> str:
    """Return a C function body by balancing braces from its definition."""
    marker = name + "("
    start = 0
    while True:
        start = source.find(marker, start)
        if start < 0:
            raise AssertionError(f"function {name} not found")
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            break
        start += len(marker)
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:pos]
    raise AssertionError(f"function {name} has an unbalanced body")


class FingerprintKernelContractTests(unittest.TestCase):
    def test_cyg1_board04_dtbo_sleep_pin_binding_contract(self):
        fixture_path = ROOT / "tools/fixtures/x810-cyg1-board04-el721-dtbo-fixture.dts"
        fixture = fixture_path.read_text(encoding="utf-8")
        driver = (ROOT / "kernel/files/egis_el721.c").read_text(encoding="utf-8")

        self.assertRegex(fixture, r'qcom,board-id\s*=\s*<\s*0x10008\s+0x04\s*>')
        self.assertRegex(fixture, r'compatible\s*=\s*"etspi,el7xx"')
        self.assertRegex(
            fixture,
            r'etspi-sleepPin\s*=\s*<&tlmm\s+0x9b\s+0x00\s*>',
        )
        self.assertRegex(fixture, r'etspi-regulator\s*=\s*"VDD_BTP_3P3"')
        self.assertNotIn("etspi-ldoPin", fixture)

        # Keep the vendor-property mapping tied to the X810 product+board
        # revision and stock compatible. Other DT descriptions and the
        # synthetic device must retain the existing generic GPIO lookup path.
        self.assertIn('of_device_is_compatible(dev->of_node, "etspi,el7xx")', driver)
        self.assertIn('of_property_read_u32_array(of_root, "qcom,board-id"', driver)
        self.assertIn("board_id[0] == 0x00010008 && board_id[1] == 4", driver)
        self.assertIn('device_property_present(dev, "etspi-sleepPin")', driver)
        self.assertIn('GPIO_LOOKUP("f100000.pinctrl", 155, "enable"', driver)
        self.assertIn("lookup->dev_id = dev_name(dev)", driver)
        self.assertIn("gpiod_add_lookup_table(lookup)", driver)
        self.assertIn("devm_add_action_or_reset(dev, el721_remove_gpio_lookup", driver)
        self.assertIn("gpiod_remove_lookup_table(data)", driver)
        self.assertIn('devm_gpiod_get(el721->dev, "enable"', driver)
        self.assertNotIn("devm_gpiod_get_from_of_node", driver)
        self.assertNotIn('"etspi-ldoPin"', driver)
        self.assertNotRegex(
            driver,
            r'GPIO_LOOKUP\([^\n]*,\s*91\s*,',
        )

        # Probe must remain metadata-only: GPIO acquisition/toggling is
        # deferred until an explicit power-on request. This protects boot
        # from a broken/incorrect experimental DT mapping.
        probe = c_function_body(driver, "el721_probe")
        power_on = c_function_body(driver, "el721_power_on_locked")
        prepare = c_function_body(driver, "el721_prepare_hardware_locked")
        self.assertNotIn("el721_prepare_hardware_locked", probe)
        self.assertNotRegex(probe, r"gpiod_(?:get|direction_output|set_value)")
        self.assertIn("el721_prepare_hardware_locked(el721)", power_on)
        self.assertIn("el721_add_x810_rev04_gpio_lookup(el721)", prepare)
        self.assertIn("gpiod_direction_output(el721->enable_gpio, 1)", power_on)

        # The stock CYG1 platform device already has a live OF node. Linux
        # device_add_of_node() refuses to replace it, so publish vdd-supply
        # directly onto that node in the regulator changeset. Keep the
        # synthetic-node attachment only for the no-OF fallback device.
        publish_rail = c_function_body(driver, "el721_publish_rail")
        self.assertIn("supply = consumer->of_node;", publish_rail)
        self.assertIn(
            'of_changeset_add_prop_u32(&el721_rail_changeset, supply,\n'
            '\t\t\t\t\t"vdd-supply",',
            publish_rail,
        )
        self.assertIn("else {\n\t\t/* The synthetic fallback device has no node of its own. */",
                      publish_rail)
        self.assertIn("if (el721_supply_node) {\n\t\tret = device_add_of_node(consumer, el721_supply_node);",
                      publish_rail)
        self.assertNotIn("device_add_of_node(consumer, el721_consumer_node)",
                         publish_rail)
        module_exit = c_function_body(driver, "el721_exit")
        self.assertLess(
            module_exit.find("platform_driver_unregister(&el721_driver)"),
            module_exit.find("platform_device_unregister(el721_rail_device)"),
        )

        # Compile and inspect the fixture on developer hosts that have dtc;
        # the source-level contract above remains dependency-free in CI.
        if not shutil.which("dtc") or not shutil.which("fdtget"):
            return
        with tempfile.TemporaryDirectory() as temporary:
            dtb = Path(temporary) / "fixture.dtb"
            subprocess.run(
                ["dtc", "-q", "-@", "-I", "dts", "-O", "dtb", "-o", str(dtb),
                 str(fixture_path)],
                check=True,
            )
            board = subprocess.check_output(
                ["fdtget", "-t", "x", str(dtb), "/", "qcom,board-id"],
                text=True,
            ).split()
            gpio = subprocess.check_output(
                ["fdtget", "-t", "x", str(dtb), "/fingerprint", "etspi-sleepPin"],
                text=True,
            ).split()
            self.assertEqual([int(value, 16) for value in board], [0x10008, 4])
            self.assertEqual([int(value, 16) for value in gpio[1:]], [155, 0])
            tlmm_path = subprocess.check_output(
                ["fdtget", "-t", "s", str(dtb), "/__symbols__", "tlmm"],
                text=True,
            ).strip()
            phandle = subprocess.check_output(
                ["fdtget", "-t", "x", str(dtb), tlmm_path, "phandle"],
                text=True,
            ).strip()
            self.assertEqual(int(phandle, 16), int(gpio[0], 16))

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
        # Kbuild silently ignores malformed Makefile variable names. The
        # earlier echo '\\t' spelling wrote the two characters backslash-t,
        # so the configured modules never entered the kernel RPM.
        for line in (
            "obj-$(CONFIG_FINGERPRINT_EL721) += egis_el721.o",
            "obj-$(CONFIG_STAR_K250A_LEGO) += snvm/",
            "obj-$(CONFIG_QCOM_SPSS) += qcom_spss.o",
            "obj-$(CONFIG_QCOM_GLINK_SPSS) += qcom_glink_spss.o",
            "obj-$(CONFIG_QCOM_SPCOM) += spcom.o",
            "obj-$(CONFIG_QCOM_SPSS_UTILS) += spss_utils.o",
            "obj-$(CONFIG_QCOM_SPSS_IRQ) += qcom_spss_irq.o",
            "obj-$(CONFIG_DMABUF_HEAPS_SP_HLOS) += qcom_sp_hlos_heap.o",
        ):
            self.assertIn(f"grep -Fqx '{line}'", prepare)
            self.assertIn(f"echo '{line}'", prepare)
        self.assertNotRegex(prepare, r"echo 'obj-[^']*\\\\t")
        self.assertIn("modules_install dtbs_install", spec)
        self.assertIn("/usr/lib/modules/*", spec)
        self.assertIn("%check", spec)
        self.assertIn('for module in egis_el721 snvm; do', spec)
        self.assertIn('required fingerprint module missing from kernel RPM', spec)


if __name__ == "__main__":
    unittest.main()
