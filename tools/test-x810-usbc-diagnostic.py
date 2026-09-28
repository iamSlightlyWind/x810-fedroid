#!/usr/bin/env python3
"""Fixture test for the read-only X810 USB-C dock diagnostic."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile


SCRIPT = Path(__file__).with_name("diagnose-x810-usbc.py")
SPEC = importlib.util.spec_from_file_location("x810_usbc_diagnostic", SCRIPT)
assert SPEC and SPEC.loader
DIAGNOSTIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAGNOSTIC)


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="x810-usbc-diagnostic-") as temp:
        root = Path(temp)
        sysfs = root / "sys"
        proc = root / "proc"

        port = sysfs / "class/typec/port0"
        partner = port / "port0-partner"
        altmode = partner / "port0-partner.0"
        write(port / "power_role", "sink\n")
        write(port / "data_role", "host\n")
        write(port / "port_type", "dual\n")
        write(partner / "supports_usb_power_delivery", "yes\n")
        write(partner / "number_of_alternate_modes", "1\n")
        write(altmode / "svid", "0xff01\n")
        write(altmode / "mode", "1\n")
        write(altmode / "active", "1\n")
        write(partner / "serial_number", "must-not-be-read\n")

        supply = sysfs / "class/power_supply/tcpm-source-psy-0-0033"
        write(supply / "type", "USB\n")
        write(supply / "online", "1\n")
        write(supply / "voltage_now", "9000000\n")
        write(supply / "current_now", "1800000\n")
        write(supply / "input_current_limit", "3000000\n")
        write(supply / "serial_number", "must-not-be-read\n")

        drm = sysfs / "class/drm/card0-DP-1"
        write(drm / "status", "connected\n")
        write(drm / "enabled", "enabled\n")
        write(drm / "modes", "2560x1440\n1920x1080\n")

        write(proc / "device-tree/model", "Samsung Galaxy Tab S9+\x00")
        write(proc / "sys/kernel/osrelease", "7.2.0-gts9wifi\n")

        before = {
            path: path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        snapshot = DIAGNOSTIC.collect_snapshot(
            sysfs,
            proc,
            label="mst-dock-powered",
            include_commands=False,
            include_kernel_log=False,
        )
        after = {
            path: path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

        assert snapshot["label"] == "mst-dock-powered"
        assert snapshot["identity"] == {
            "device_model": "Samsung Galaxy Tab S9+",
            "kernel_release": "7.2.0-gts9wifi",
        }
        attrs = {item["path"]: item for item in snapshot["typec"]}
        assert attrs[str(port)]["power_role"] == "sink"
        assert attrs[str(port)]["data_role"] == "host"
        assert attrs[str(altmode)]["svid"] == "0xff01"
        assert attrs[str(altmode)]["active"] == "1"
        assert "serial_number" not in str(snapshot)
        supplies = {item["path"]: item for item in snapshot["power_supplies"]}
        assert supplies[str(supply)]["voltage_now"] == "9000000"
        assert supplies[str(supply)]["input_current_limit"] == "3000000"
        connectors = {item["path"]: item for item in snapshot["drm_connectors"]}
        assert connectors[str(drm)]["status"] == "connected"
        assert "usb_tree" not in snapshot and "kernel_log" not in snapshot
        assert "tcpm_debugfs" not in snapshot, "TCPM logs must be opt-in"
        assert before == after, "diagnostic must not modify sysfs/procfs fixture files"

        debugfs_usb = sysfs / "kernel/debug/usb"
        first_log = debugfs_usb / "tcpm-i2c-9-0033/log"
        second_log = debugfs_usb / "tcpm-i2c-10-0033/log"
        unrelated_log = debugfs_usb / "dwc3-9a00000.usb/log"
        write(first_log, "[1.0] Source_Capabilities\n[1.1] Requested 9000 mV, 1800 mA\n")
        write(second_log, "[2.0] PR_SWAP accepted\n")
        write(unrelated_log, "must-not-be-read\n")

        before_tcpm = {
            path: path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

        # The opt-in collector must not invoke any command or attempt to mount
        # debugfs.  It only opens the exact tcpm-*/log files once.
        original_run_read_only = DIAGNOSTIC.run_read_only_command
        original_subprocess_run = subprocess.run

        def unexpected_command(*args, **kwargs):
            raise AssertionError("TCPM capture must not run commands or mount debugfs")

        DIAGNOSTIC.run_read_only_command = unexpected_command
        subprocess.run = unexpected_command
        try:
            tcpm_snapshot = DIAGNOSTIC.collect_snapshot(
                sysfs,
                proc,
                label="mst-dock-powered",
                include_commands=False,
                include_kernel_log=False,
                include_tcpm_log=True,
            )
        finally:
            DIAGNOSTIC.run_read_only_command = original_run_read_only
            subprocess.run = original_subprocess_run

        capture = tcpm_snapshot["tcpm_debugfs"]
        assert capture["available"] is True
        assert "advances/consumes" in capture["warning"]
        logs = {item["path"]: item["text"] for item in capture["logs"]}
        assert list(logs) == sorted([str(first_log), str(second_log)])
        assert "Source_Capabilities" in logs[str(first_log)]
        assert "PR_SWAP accepted" in logs[str(second_log)]
        assert str(unrelated_log) not in logs
        after_tcpm = {
            path: path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        assert before_tcpm == after_tcpm, "TCPM diagnostic must not write fixture files"

        # Missing debugfs is reported as unavailable; the collector does not
        # mount it or invoke a helper command to make it available.
        missing_sysfs = root / "missing-sys"
        missing_sysfs.mkdir()
        before_missing = {
            path: path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        DIAGNOSTIC.run_read_only_command = unexpected_command
        subprocess.run = unexpected_command
        try:
            unavailable = DIAGNOSTIC.collect_snapshot(
                missing_sysfs,
                proc,
                include_commands=False,
                include_kernel_log=False,
                include_tcpm_log=True,
            )["tcpm_debugfs"]
        finally:
            DIAGNOSTIC.run_read_only_command = original_run_read_only
            subprocess.run = original_subprocess_run
        after_missing = {
            path: path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        assert unavailable["available"] is False
        assert "not mounted" in unavailable["reason"]
        assert unavailable["logs"] == []
        assert before_missing == after_missing

        args = DIAGNOSTIC.parse_args(["--include-tcpm-log", "--no-commands", "--no-kernel-log"])
        assert args.include_tcpm_log is True

    print("X810 USB-C diagnostic fixture and read-only/opt-in TCPM log contract passed")


if __name__ == "__main__":
    main()
