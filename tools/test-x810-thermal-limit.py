#!/usr/bin/env python3
"""Exercise X810 profile and thermal limits against temporary sysfs fixtures."""

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


def performance_settings(max_percent=100, cpu_boost=True, gpu_boost=True):
    return {
        "version": 1,
        "profiles": {
            profile: {
                "max_percent": max_percent,
                "cpu_boost": cpu_boost,
                "gpu_boost": gpu_boost,
            }
            for profile in THERMAL.PROFILE_IDS
        },
    }


class ThermalLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.thermal = self.root / "thermal"
        self.cpu = self.root / "cpu"
        self.gpu = self.root / "gpu"
        self.boost = self.cpu / "boost"
        self.config = self.root / "thermal-settings.json"
        self.performance_config = self.root / "profile-settings.json"
        self.run = self.root / "run"
        self.add_zone("cpu0-thermal", 54000)
        self.add_zone("gpuss-0-thermal", 50000)
        self.add_zone("sm5714-battery", 70000)
        self.add_policy("policy0", 2000000, 500000, [500000, 1000000, 1500000, 2000000])
        self.add_policy("policy3", 2800000, 600000, [600000, 1200000, 1800000, 2400000, 2800000])
        put(self.boost, 0)
        self.add_gpu()
        self.config.write_text(json.dumps({"enabled": True, "threshold_c": 55}), encoding="utf-8")
        self.performance_config.write_text(json.dumps(performance_settings()), encoding="utf-8")
        self.active_profile = "balanced"
        self.limiter = self.make_limiter()

    def tearDown(self):
        self.temp.cleanup()

    def make_limiter(self):
        return THERMAL.Limiter(
            self.thermal, self.cpu, self.config, self.run,
            performance_config_path=self.performance_config,
            gpu_root=self.gpu,
            cpu_boost_path=self.boost,
            profile_reader=lambda: self.active_profile,
        )

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

    def add_gpu(self):
        put(self.gpu / "min_freq", 220000000)
        put(self.gpu / "max_freq", 680000000)
        put(
            self.gpu / "available_frequencies",
            "220000000 295000000 348000000 401000000 475000000 550000000 615000000 680000000",
        )

    def test_hottest_soc_sensor_selected_but_battery_is_excluded(self):
        self.assertEqual(THERMAL.read_soc_temperature(self.thermal), 54.0)
        self.set_temp("gpuss-0-thermal", 61000)
        self.assertEqual(THERMAL.read_soc_temperature(self.thermal), 61.0)

    def test_balanced_defaults_limit_cpu_gpu_and_disable_boost(self):
        settings = THERMAL.read_performance_settings(self.root / "missing.json")
        self.assertEqual(settings["profiles"]["balanced"], {
            "max_percent": 75, "cpu_boost": False, "gpu_boost": False,
        })
        self.performance_config.unlink()
        status = self.limiter.step()
        self.assertEqual(status["profile"], "balanced")
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1500000")
        self.assertEqual((self.cpu / "policy3/scaling_max_freq").read_text().strip(), "1800000")
        self.assertEqual((self.boost).read_text().strip(), "0")
        self.assertEqual((self.gpu / "max_freq").read_text().strip(), "475000000")

    def test_profile_change_applies_saved_ceiling_and_boosts(self):
        settings = performance_settings(100, True, True)
        settings["profiles"]["balanced"] = {
            "max_percent": 70, "cpu_boost": False, "gpu_boost": False,
        }
        self.performance_config.write_text(json.dumps(settings), encoding="utf-8")
        balanced = self.limiter.step()
        self.assertEqual(balanced["max_percent"], 70)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1000000")
        self.assertEqual((self.boost).read_text().strip(), "0")
        self.assertEqual((self.gpu / "max_freq").read_text().strip(), "475000000")

        self.active_profile = "performance"
        performance = self.limiter.step()
        self.assertEqual(performance["profile"], "performance")
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "2000000")
        self.assertEqual((self.boost).read_text().strip(), "1")
        self.assertEqual((self.gpu / "max_freq").read_text().strip(), "680000000")

    def test_spike_does_not_cap_and_sustained_heat_caps_cpu_and_gpu(self):
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.assertFalse(self.limiter.limited)
        status = self.limiter.step()
        self.assertTrue(self.limiter.limited)
        self.assertTrue(status["limited"])
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1500000")
        self.assertEqual((self.cpu / "policy3/scaling_max_freq").read_text().strip(), "1800000")
        self.assertEqual((self.gpu / "max_freq").read_text().strip(), "475000000")
        self.assertEqual((self.cpu / "policy0/scaling_min_freq").read_text().strip(), "500000")
        self.assertEqual((self.cpu / "policy0/scaling_governor").read_text().strip(), "performance")

    def test_three_degree_hysteresis_restores_selected_profile_limits(self):
        settings = performance_settings(75, False, False)
        self.performance_config.write_text(json.dumps(settings), encoding="utf-8")
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.limiter.step()
        self.assertTrue(self.limiter.limited)
        self.set_temp("cpu0-thermal", 52000)
        self.limiter.step()
        self.assertTrue(self.limiter.limited)
        self.limiter.step()
        self.assertFalse(self.limiter.limited)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1500000")
        self.assertEqual((self.gpu / "max_freq").read_text().strip(), "475000000")

    def test_disable_and_missing_sensor_remove_thermal_reduction_but_keep_profile_caps(self):
        self.performance_config.write_text(
            json.dumps(performance_settings(75, False, False)), encoding="utf-8"
        )
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.limiter.step()
        self.config.write_text(json.dumps({"enabled": False, "threshold_c": 55}), encoding="utf-8")
        self.limiter.step()
        self.assertFalse(self.limiter.limited)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "1500000")
        for zone in self.thermal.glob("thermal_zone*"):
            (zone / "type").write_text("battery-thermal", encoding="ascii")
        status = self.limiter.step()
        self.assertEqual(status["error"], "No readable SoC thermal sensors")
        self.assertEqual((self.gpu / "max_freq").read_text().strip(), "475000000")

    def test_invalid_thermal_configuration_fails_open_but_keeps_profile_limits(self):
        self.config.write_text('{"enabled":true,"threshold_c":15}', encoding="utf-8")
        self.set_temp("cpu0-thermal", 85000)
        status = self.limiter.step()
        self.assertFalse(status["enabled"])
        self.assertFalse(self.limiter.limited)
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "2000000")

    def test_saved_apply_marker_recovers_cpu_after_process_restart(self):
        self.set_temp("cpu0-thermal", 56000)
        self.limiter.step()
        self.limiter.step()
        self.assertTrue(json.loads((self.run / "cpu-max-baselines.json").read_text())["applied"])
        restarted = self.make_limiter()
        self.assertEqual((self.cpu / "policy0/scaling_max_freq").read_text().strip(), "2000000")
        self.assertFalse(json.loads((self.run / "cpu-max-baselines.json").read_text())["applied"])
        self.assertFalse(restarted.limited)

    def test_profile_settings_are_strictly_validated(self):
        valid = performance_settings()
        self.assertIsNotNone(THERMAL.valid_performance_settings(valid))
        invalid = performance_settings()
        invalid["profiles"]["balanced"]["max_percent"] = 101
        self.assertIsNone(THERMAL.valid_performance_settings(invalid))
        invalid = performance_settings()
        invalid["profiles"]["balanced"]["cpu_boost"] = 1
        self.assertIsNone(THERMAL.valid_performance_settings(invalid))

    def test_device_service_writes_gpu_and_cpu_sysfs_and_is_enabled(self):
        service = ROOT / "rootfs/overlay/usr/lib/systemd/system/x810-thermal-limit.service"
        preset = ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/85-gts9wifi.preset"
        spec = (ROOT / "specs/x810-fedora-port.spec").read_text(encoding="utf-8")
        self.assertTrue(service.is_file())
        self.assertIn("enable x810-thermal-limit.service", preset.read_text(encoding="utf-8"))
        self.assertIn("systemctl enable x810-thermal-limit.service", spec)
        self.assertIn("systemctl start x810-thermal-limit.service", spec)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("scaling_max_freq", source)
        self.assertIn("available_frequencies", source)
        self.assertNotIn("trip_point_", source)
        self.assertIn("/sys/devices/platform/soc@0/3d00000.gpu/devfreq", service.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
