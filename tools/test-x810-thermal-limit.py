#!/usr/bin/env python3
"""Exercise the X810 thermal policy against temporary sysfs fixtures."""

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rootfs/overlay/usr/libexec/x810-thermal-limit"
SPEC = importlib.util.spec_from_loader(
    "x810_thermal_limit", SourceFileLoader("x810_thermal_limit", str(SCRIPT))
)
THERMAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(THERMAL)


def put(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="ascii")


class ThermalLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.thermal = self.root / "thermal"
        self.cpu = self.root / "cpu"
        self.config = self.root / "settings.json"
        self.run = self.root / "run"
        self.add_zone("cpu0-thermal", 54000)
        self.add_zone("gpuss-0-thermal", 50000)
        self.add_zone("sm5714-battery", 70000)
        self.add_policy("policy0", 2000000, 500000, [500000, 1000000, 1500000, 2000000])
        self.add_policy("policy3", 2800000, 600000, [600000, 1200000, 1800000, 2400000, 2800000])
        self.config.write_text(json.dumps({"enabled": True, "threshold_c": 55}), encoding="utf-8")
        self.limiter = THERMAL.Limiter(self.thermal, self.cpu, self.config, self.run)

    def tearDown(self):
        self.temp.cleanup()

    def add_zone(self, name, milli_c):
        zone = self.thermal / f"thermal_zone{len(list(self.thermal.glob('thermal_zone*')))}"
        put(zone / "type", name)
        put(zone / "temp", milli_c)
        return zone

    def set_temp(self, zone_name, milli_c):
        for zone in self.thermal.glob("thermal_zone*"):
            if (zone / "type").read_text().strip() == zone_name:
                put(zone / "temp", milli_c)
                return
        raise AssertionError(zone_name)

    def add_policy(self, name, maximum, minimum, available):
        policy = self.cpu / name
        put(policy / "scaling_max_freq", maximum)
        put(policy / "cpuinfo_max_freq", maximum)
        put(policy / "scaling_min_freq", minimum)
        put(policy / "scaling_governor", "performance")
        put(policy / "scaling_available_frequencies", " ".join(map(str, available)))

    def test_hottest_soc_sensor_selected_but_battery_is_excluded(self):
        self.assertEqual(THERMAL.read_soc_temperature(self.thermal), 54.0)
        self.set_temp("gpuss-0-thermal", 61000)
        self.assertEqual(THERMAL.read_soc_temperature(self.thermal), 61.0)

    def test_spike_does_not_cap_and_sustained_hot_temperature_does(self):
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.assertFalse(self.limiter.limited)
        self.limiter.step()
        self.assertTrue(self.limiter.limited)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1500000")
        self.assertEqual((self.cpu / "policy3/scaling_max_freq").read_text().strip(), "1800000")
        self.assertEqual((self.cpu / "policy0/scaling_min_freq").read_text().strip(), "500000")
        self.assertEqual((self.cpu / "policy0/scaling_governor").read_text().strip(), "performance")

    def test_three_degree_hysteresis_and_restore(self):
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.limiter.step()
        self.set_temp("cpu0-thermal", 52000)
        self.limiter.step()
        self.assertTrue(self.limiter.limited)
        self.limiter.step()
        self.assertFalse(self.limiter.limited)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "2000000")

    def test_disable_and_missing_sensor_restore_original_policy(self):
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.limiter.step()
        self.config.write_text(json.dumps({"enabled": False, "threshold_c": 55}), encoding="utf-8")
        self.limiter.step()
        self.assertFalse(self.limiter.limited)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "2000000")
        for zone in self.thermal.glob("thermal_zone*"):
            (zone / "type").write_text("battery-thermal", encoding="ascii")
        self.limiter.step()
        self.assertEqual(self.limiter.step()["error"], "No readable SoC thermal sensors")

    def test_idle_external_cpu_cap_is_preserved_and_becomes_thermal_baseline(self):
        put(self.cpu / "policy0/scaling_max_freq", 1785600)
        self.limiter.step()
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1785600")
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.limiter.step()
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1000000")
        self.set_temp("cpu0-thermal", 52000)
        self.limiter.step()
        self.limiter.step()
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1785600")

    def test_saved_apply_marker_recovers_cpus_after_process_restart(self):
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.limiter.step()
        self.assertTrue(json.loads((self.run / "cpu-max-baselines.json").read_text())["applied"])
        restarted = THERMAL.Limiter(self.thermal, self.cpu, self.config, self.run)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "2000000")
        self.assertFalse(json.loads((self.run / "cpu-max-baselines.json").read_text())["applied"])

    def test_invalid_configuration_fails_open(self):
        self.config.write_text('{"enabled":true,"threshold_c":15}', encoding="utf-8")
        self.set_temp("cpu0-thermal", 85000)
        status = self.limiter.step()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["limited"])
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "2000000")

    def test_device_service_is_part_of_fresh_install_and_support_updates(self):
        service = ROOT / "rootfs/overlay/usr/lib/systemd/system/x810-thermal-limit.service"
        preset = ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/85-gts9wifi.preset"
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertTrue(service.is_file())
        self.assertIn("enable x810-thermal-limit.service", preset.read_text(encoding="utf-8"))
        self.assertIn("systemctl enable x810-thermal-limit.service", spec)
        self.assertIn("systemctl start x810-thermal-limit.service", spec)
        self.assertIn("scaling_max_freq", SCRIPT.read_text(encoding="utf-8"))
        self.assertNotIn("trip_point_", SCRIPT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
