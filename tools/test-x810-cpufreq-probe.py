#!/usr/bin/env python3
"""Guard the SM8550 interconnect dependency behind X810 CPUFreq policies."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CpuFreqProbeContract(unittest.TestCase):
    def test_osm_l3_provider_is_loaded_for_new_and_existing_installs(self):
        module_file = ROOT / "rootfs/overlay/etc/modules-load.d/x810-cpufreq.conf"
        self.assertTrue(module_file.is_file())
        self.assertRegex(module_file.read_text(), r"(?m)^icc_osm_l3\s*$")

        spec = (ROOT / "specs/x810-fedora-port.spec").read_text()
        self.assertIn("modprobe icc_osm_l3", spec)
        self.assertIn("systemctl enable tuned.service tuned-ppd.service", spec)

        rootfs = (ROOT / "rootfs/build-rootfs.sh").read_text()
        support = (ROOT / "tools/build-port-support-rpm.sh").read_text()
        self.assertIn('cp -a "$repo_dir/rootfs/overlay/." "$rootfs/"', rootfs)
        self.assertIn('cp -a "$repo_dir/rootfs/overlay/." "$stage/"', support)

    def test_kernel_build_contains_cpu_frequency_and_sm8550_interconnect(self):
        base = (ROOT / "kernel/files/config-mainline.aarch64").read_text()
        self.assertIn("CONFIG_ARM_QCOM_CPUFREQ_HW=y", base)
        self.assertIn("CONFIG_INTERCONNECT_QCOM_SM8550=y", base)
        self.assertIn("CONFIG_INTERCONNECT_QCOM_OSM_L3=m", base)
        kernel_spec = (ROOT / "kernel/kernel.spec").read_text()
        self.assertIn("modules_install", kernel_spec)
        self.assertIn("/usr/lib/modules/*", kernel_spec)


if __name__ == "__main__":
    unittest.main()
