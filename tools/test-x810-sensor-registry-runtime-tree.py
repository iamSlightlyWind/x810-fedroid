#!/usr/bin/env python3
"""Test the exact-X810 runtime HexagonFS composer without touching a device."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import runpy
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-sensor-registry-perms"
# run_path executes the helper without leaving a __pycache__ inside the
# production overlay (which build-rootfs copies verbatim into the image).
sensor_tree = SimpleNamespace(**runpy.run_path(str(HELPER)))


class Fixture:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="x810-sensor-tree-")
        self.root = Path(self.tmp.name)
        self.base = self.root / "base"
        self.vendor = self.root / "vendor/etc/sensors"
        self.persist_sensor = self.root / "persist/sensors/registry"
        self.socinfo = self.root / "sys/devices/soc0"
        self.run = self.root / "run"
        self.config = self.vendor / "config"
        self.output = self.persist_sensor / "registry"
        self.config.mkdir(parents=True)
        self.output.mkdir(parents=True)
        self.socinfo.mkdir(parents=True)
        (self.base / "dsp/adsp").mkdir(parents=True)
        (self.base / "sensors/config").mkdir(parents=True)
        (self.base / "sensors/config/sibling-only.json").write_text("wrong model\n")
        (self.base / "dsp/adsp/libsns_dynamic_loader_skel.so").write_bytes(b"shared skel")

        self.configs = {
            "kailua_lsm6dso_0_0.json": b'{"orientation":"+y,-x,+z"}\n',
            "kailua_ak991x_0.json": b'{"orientation":"-x,+y,-z"}\n',
        }
        timestamp = 1640995200
        for name, content in self.configs.items():
            path = self.config / name
            path.write_bytes(content)
            os.utime(path, (timestamp, timestamp))

        vendor_config = (
            "version=6\n"
            "file=hw_platform=/sys/devices/soc0/hw_platform\n"
            "file=platform_subtype=/sys/devices/soc0/platform_subtype\n"
            "file=platform_subtype_id=/sys/devices/soc0/platform_subtype_id\n"
            "file=platform_version=/sys/devices/soc0/platform_version\n"
            "file=soc_id=/sys/devices/soc0/soc_id\n"
            "file=output=/mnt/vendor/persist/sensors/registry/registry\n"
        )
        (self.vendor / "sns_reg_config").write_text(vendor_config)
        self.cache = self.output / "sns_reg_config"
        self.cache.write_text(json.dumps({"sns_reg_config": {
            "owner": "NA",
            **{
                f"/vendor/etc/sensors/config/{name}": {
                    "type": "int", "ver": "0", "data": str(timestamp)
                }
                for name in self.configs
            },
        }}))
        (self.output / "sensors_registry").write_bytes(b"")
        (self.output / "default_sensors.accel").write_bytes(b"tablet-specific registry")
        (self.persist_sensor / "sns_reg_version").write_bytes(b"version=6\0")

        (self.socinfo / "hw_platform").write_text("MTP\n")
        (self.socinfo / "platform_subtype").write_text("Unknown\n")
        (self.socinfo / "platform_subtype_id").write_text("0\n")
        (self.socinfo / "platform_version").write_text("65536\n")
        (self.socinfo / "soc_id").write_text("519\n")

    def stage(self) -> Path:
        return sensor_tree.stage_runtime_tree(
            base=self.base,
            vendor_sensor=self.vendor,
            persist_sensor=self.persist_sensor,
            socinfo=self.socinfo,
            run_dir=self.run,
            fastrpc_uid=os.getuid(),
            fastrpc_gid=os.getgid(),
        )

    def close(self) -> None:
        self.tmp.cleanup()


def check(condition: bool, explanation: str) -> None:
    if not condition:
        raise AssertionError(explanation)


def test_runtime_tree_uses_vendor_and_own_persist_without_sibling_config() -> None:
    fixture = Fixture()
    try:
        cache_before = fixture.cache.read_bytes()
        link = fixture.stage()
        tree = link.resolve()
        check(link.is_symlink(), "runtime tree should be atomically published as a symlink")
        check(
            (tree / "sensors/config/kailua_lsm6dso_0_0.json").read_bytes()
            == fixture.configs["kailua_lsm6dso_0_0.json"],
            "X810 vendor config was not staged verbatim",
        )
        check(
            int((tree / "sensors/config/kailua_lsm6dso_0_0.json").stat().st_mtime)
            == 1640995200,
            "vendor config mtime must be preserved to agree with stock persist cache",
        )
        check(
            not (tree / "sensors/config/sibling-only.json").exists(),
            "sibling-model config leaked into the runtime tree",
        )
        check(
            (tree / "sensors/registry/default_sensors.accel").read_bytes()
            == b"tablet-specific registry",
            "the tablet's own generated persist registry was not staged",
        )
        check(
            (tree / "sensors/registry/sensors_registry").stat().st_size == 0,
            "persist completion marker changed",
        )
        check(
            (tree / "sensors/sns_reg_version").read_bytes() == b"version=6\0",
            "stock registry version was not staged at the mapped path",
        )
        check((tree / "socinfo/hw_platform").read_text() == "MTP\n", "socinfo selector mismatch")
        check((tree / "socinfo/platform_subtype").read_text() == "Unknown\n",
              "socinfo platform subtype mismatch")
        check((tree / "socinfo/platform_subtype_id").read_text() == "0\n",
              "socinfo platform subtype ID mismatch")
        check((tree / "socinfo/platform_version").read_text() == "65536\n",
              "socinfo platform version mismatch")
        check((tree / "socinfo/soc_id").read_text() == "519\n", "socinfo selector mismatch")
        check((tree / "dsp/adsp/libsns_dynamic_loader_skel.so").read_bytes() == b"shared skel",
              "shared ADSP skeleton was not retained")
        check(fixture.cache.read_bytes() == cache_before,
              "staging modified the source Android persist registry")
    finally:
        fixture.close()


def test_missing_android_socinfo_aliases_use_twrp_captured_values() -> None:
    fixture = Fixture()
    try:
        for name in (
            "hw_platform",
            "platform_subtype",
            "platform_subtype_id",
            "platform_version",
            "soc_id",
        ):
            (fixture.socinfo / name).unlink(missing_ok=True)
        tree = fixture.stage().resolve()
        expected = {
            "hw_platform": "MTP",
            "platform_subtype": "Unknown",
            "platform_subtype_id": "0",
            "platform_version": "65536",
            "soc_id": "519",
        }
        for name, value in expected.items():
            check(
                (tree / "socinfo" / name).read_text() == value + "\n",
                f"missing Android-era socinfo alias {name} was not staged from the TWRP-captured value",
            )
        # The X810-specific sensor nodes in the fixture use the CYG1 selector
        # domain (MTP and soc_id 519/536) used by the stock kailua configs.
        selectors = {"hw_platform": {"MTP", "Surf", "RCM"}, "soc_id": {"519", "536"}}
        for key, allowed in selectors.items():
            check(expected[key] in allowed, f"derived selector {key} is not accepted by CYG1 configs")
    finally:
        fixture.close()


def test_twrp_captured_socinfo_agrees_with_checked_in_x810_dts() -> None:
    dts = (ROOT / "kernel/files/sm8550-samsung-gts9wifi.dts").read_text(
        encoding="utf-8"
    )
    msm_id = re.search(r"qcom,msm-id\s*=\s*<([^>]+)>", dts)
    check(msm_id is not None, "checked-in X810 DTS has no qcom,msm-id list")
    ids = {int(value, 0) for value in msm_id.group(1).split()}
    check("qcom,kalama-mtp" in dts, "checked-in X810 DTS no longer identifies Kalama MTP")
    check(0x207 in ids, "checked-in X810 DTS no longer lists Qualcomm soc_id 519")
    check(sensor_tree.SOCINFO_FALLBACK["hw_platform"] == "MTP",
          "captured socinfo disagrees with X810 kalama-mtp compatible")
    check(sensor_tree.SOCINFO_FALLBACK["soc_id"] == "519",
          "captured socinfo disagrees with X810 qcom,msm-id 0x207")


def test_stale_persist_cache_fails_closed_before_publishing() -> None:
    fixture = Fixture()
    try:
        path = fixture.config / "kailua_lsm6dso_0_0.json"
        os.utime(path, (1640995201, 1640995201))
        try:
            fixture.stage()
        except sensor_tree.StageError as exc:
            check("registry cache is stale" in str(exc), f"unexpected failure: {exc}")
        else:
            raise AssertionError("stale registry cache must not start sensorspd")
        check(not (fixture.run / "gts9wifi-hexagonfs").exists(),
              "invalid inputs published a runtime HexagonFS tree")
    finally:
        fixture.close()


def test_missing_persist_completion_marker_fails_closed() -> None:
    fixture = Fixture()
    try:
        (fixture.output / "sensors_registry").unlink()
        try:
            fixture.stage()
        except sensor_tree.StageError as exc:
            check("completion marker" in str(exc), f"unexpected failure: {exc}")
        else:
            raise AssertionError("missing completion marker must fail closed")
    finally:
        fixture.close()


def test_unexpected_vendor_registry_version_fails_closed() -> None:
    fixture = Fixture()
    try:
        path = fixture.vendor / "sns_reg_config"
        path.write_text(path.read_text().replace("version=6", "version=5"))
        try:
            fixture.stage()
        except sensor_tree.StageError as exc:
            check("unsupported" in str(exc), f"unexpected failure: {exc}")
        else:
            raise AssertionError("unexpected stock registry format must not start sensorspd")
        check(not (fixture.run / "gts9wifi-hexagonfs").exists(),
              "unsupported inputs published a runtime HexagonFS tree")
    finally:
        fixture.close()


def test_incomplete_socinfo_selector_set_fails_closed() -> None:
    fixture = Fixture()
    try:
        config = fixture.vendor / "sns_reg_config"
        config.write_text(
            "version=6\n"
            "file=hw_platform=/sys/devices/soc0/hw_platform\n"
            "file=platform_subtype=/sys/devices/soc0/platform_subtype\n"
            "file=platform_subtype_id=/sys/devices/soc0/platform_subtype_id\n"
            "file=soc_id=/sys/devices/soc0/soc_id\n"
            "file=output=/mnt/vendor/persist/sensors/registry/registry\n"
        )
        try:
            fixture.stage()
        except sensor_tree.StageError as exc:
            check("selector set" in str(exc), "unexpected selector failure reason")
            check(not (fixture.run / "gts9wifi-hexagonfs").exists(),
                  "incomplete stock selectors published a runtime tree")
        else:
            raise AssertionError("incomplete stock socinfo selector set was accepted")
    finally:
        fixture.close()


def main() -> None:
    test_runtime_tree_uses_vendor_and_own_persist_without_sibling_config()
    test_missing_android_socinfo_aliases_use_twrp_captured_values()
    test_twrp_captured_socinfo_agrees_with_checked_in_x810_dts()
    test_stale_persist_cache_fails_closed_before_publishing()
    test_missing_persist_completion_marker_fails_closed()
    test_unexpected_vendor_registry_version_fails_closed()
    test_incomplete_socinfo_selector_set_fails_closed()
    print("PASS: runtime HexagonFS uses exact vendor config + matching tablet persist registry")
    print("PASS: stale/missing registry inputs fail closed without touching Android persist")


if __name__ == "__main__":
    main()
