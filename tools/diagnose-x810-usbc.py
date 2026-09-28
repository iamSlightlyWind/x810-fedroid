#!/usr/bin/env python3
"""Collect a read-only USB-C / USB-PD / MST snapshot for the X810 port.

The tool only reads selected sysfs attributes and kernel logs, plus runs the
read-only ``lsusb -t`` command when available.  It never writes sysfs, invokes
I2C tools, changes roles, resets controllers, or requests a charger contract.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


TYPEC_ATTRIBUTES = {
    "name", "power_role", "data_role", "port_type", "preferred_role",
    "accessory_mode", "vconn_source", "usb_power_delivery_revision",
    "supports_usb_power_delivery", "number_of_alternate_modes", "svid",
    "mode", "active", "orientation", "usb_type",
}
POWER_SUPPLY_ATTRIBUTES = {
    "name", "type", "status", "online", "usb_type", "voltage_now",
    "current_now", "power_now", "voltage_max", "voltage_min",
    "voltage_max_design", "current_max", "input_voltage_limit",
    "input_current_limit", "charge_type", "health",
}
DRM_ATTRIBUTES = {"status", "enabled", "dpms", "modes"}
LOG_PATTERN = re.compile(
    r"sm5714|sm5440|tcpm|type.?c|usb.?pd|pdic|alt.?mode|displayport|"
    r"\bdrm\b.*\b(dp|hpd|mst)\b|\b(dp|hpd|mst)\b.*\bdrm\b|"
    r"vbus|ps5169|xhci|dwc3",
    re.IGNORECASE,
)


def read_attribute(path: Path) -> str | None:
    """Read one whitelisted sysfs/procfs text attribute, handling absence."""
    try:
        return path.read_bytes().decode("utf-8", errors="replace").strip("\x00\n\r ")
    except (OSError, UnicodeError):
        return None


def _walk_attributes(
    class_root: Path,
    attributes: set[str],
    *,
    max_depth: int,
    max_directories: int = 128,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not class_root.is_dir():
        return records

    visited: set[str] = set()
    remaining = max_directories

    def visit(directory: Path, depth: int) -> None:
        nonlocal remaining
        if remaining <= 0 or depth > max_depth:
            return
        try:
            realpath = os.path.realpath(directory)
            if realpath in visited:
                return
            visited.add(realpath)
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError:
            return

        remaining -= 1
        record: dict[str, Any] = {"path": str(directory)}
        for child in children:
            if child.name in attributes and child.is_file():
                value = read_attribute(child)
                if value is not None:
                    record[child.name] = value
        if len(record) > 1:
            records.append(record)

        if depth < max_depth:
            for child in children:
                if child.is_dir():
                    visit(child, depth + 1)

    for entry in sorted(class_root.iterdir(), key=lambda child: child.name):
        if entry.is_dir():
            visit(entry, 0)
    return records


def collect_typec(sysfs_root: Path) -> list[dict[str, Any]]:
    # Type-C ports have partner/cable/alternate-mode child objects.  A bounded
    # depth is enough to include those without recursively walking device sysfs.
    return _walk_attributes(
        sysfs_root / "class/typec", TYPEC_ATTRIBUTES, max_depth=3
    )


def collect_power_supplies(sysfs_root: Path) -> list[dict[str, Any]]:
    return _walk_attributes(
        sysfs_root / "class/power_supply", POWER_SUPPLY_ATTRIBUTES,
        max_depth=0,
    )


def collect_drm(sysfs_root: Path) -> list[dict[str, Any]]:
    return _walk_attributes(sysfs_root / "class/drm", DRM_ATTRIBUTES, max_depth=0)


def collect_identity(proc_root: Path) -> dict[str, str]:
    identity: dict[str, str] = {}
    model = read_attribute(proc_root / "device-tree/model")
    release = read_attribute(proc_root / "sys/kernel/osrelease")
    if model:
        identity["device_model"] = model
    if release:
        identity["kernel_release"] = release
    return identity


def run_read_only_command(command: list[str], timeout: int = 10) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if not executable:
        return {"available": False, "reason": f"{command[0]} not installed"}
    try:
        result = subprocess.run(
            [executable, *command[1:]], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": True, "error": str(error)}
    return {
        "available": True,
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def collect_kernel_log() -> dict[str, Any]:
    """Return only filtered current-boot USB-C/PD/DP log lines."""
    result = run_read_only_command(
        ["journalctl", "-k", "-b", "-n", "2000", "-o", "short-monotonic",
         "--no-pager"],
        timeout=15,
    )
    source = "journalctl"
    if not result.get("available") or result.get("exit_code") != 0:
        result = run_read_only_command(["dmesg", "--color=never"], timeout=10)
        source = "dmesg"
    if result.get("exit_code") != 0:
        return {"source": source, "error": result.get("stderr", "log unavailable")}

    lines = result.get("stdout", "").splitlines()
    matches = [line for line in lines[-2000:] if LOG_PATTERN.search(line)]
    return {"source": source, "matched_lines": matches[-250:]}


def collect_tcpm_debugfs_logs(sysfs_root: Path) -> dict[str, Any]:
    """Read each available TCPM debugfs log once, without mounting debugfs.

    TCPM's debugfs show method advances the log buffer tail after a successful
    read.  Callers should therefore capture it once per attachment state and
    retain the returned text rather than repeatedly opening the same log.
    """
    usb_debug_root = sysfs_root / "kernel/debug/usb"
    warning = (
        "Reading a TCPM debugfs log advances/consumes its buffered entries; "
        "capture once per state and save this output. Debugfs is never mounted."
    )

    if not usb_debug_root.is_dir():
        return {
            "available": False,
            "reason": "TCPM debugfs directory unavailable (not mounted or inaccessible)",
            "warning": warning,
            "logs": [],
        }

    try:
        tcpm_dirs = sorted(
            child for child in usb_debug_root.iterdir()
            if child.name.startswith("tcpm-") and child.is_dir()
        )
    except OSError as error:
        return {
            "available": False,
            "reason": f"TCPM debugfs directory unreadable: {error}",
            "warning": warning,
            "logs": [],
        }

    logs: list[dict[str, str]] = []
    readable = 0
    for tcpm_dir in tcpm_dirs:
        log_path = tcpm_dir / "log"
        if not log_path.is_file():
            continue
        try:
            contents = log_path.read_bytes().decode("utf-8", errors="replace")
        except OSError as error:
            logs.append({"path": str(log_path), "error": str(error)})
            continue
        logs.append({"path": str(log_path), "text": contents.strip("\x00\n\r ")})
        readable += 1

    if not readable:
        return {
            "available": False,
            "reason": "no readable tcpm-*/log entries found",
            "warning": warning,
            "logs": logs,
        }

    return {"available": True, "warning": warning, "logs": logs}


def collect_snapshot(
    sysfs_root: Path,
    proc_root: Path,
    *,
    label: str | None = None,
    include_commands: bool = True,
    include_kernel_log: bool = True,
    include_tcpm_log: bool = False,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "safety": "read-only; no sysfs writes, raw I2C, role changes, or PD requests",
        "identity": collect_identity(proc_root),
        "typec": collect_typec(sysfs_root),
        "power_supplies": collect_power_supplies(sysfs_root),
        "drm_connectors": collect_drm(sysfs_root),
    }
    if label:
        snapshot["label"] = label
    if include_commands:
        snapshot["usb_tree"] = run_read_only_command(["lsusb", "-t"])
    if include_kernel_log:
        snapshot["kernel_log"] = collect_kernel_log()
    if include_tcpm_log:
        snapshot["tcpm_debugfs"] = collect_tcpm_debugfs_logs(sysfs_root)
    return snapshot


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", help="state being captured (for example, mst-dock-with-pd)")
    parser.add_argument("--sysfs-root", type=Path, default=Path("/sys"),
                        help="sysfs root (default: /sys; primarily useful for tests)")
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"),
                        help="procfs root (default: /proc; primarily useful for tests)")
    parser.add_argument("--no-commands", action="store_true",
                        help="skip the read-only lsusb -t command")
    parser.add_argument("--no-kernel-log", action="store_true",
                        help="skip current-boot kernel log collection")
    parser.add_argument(
        "--include-tcpm-log", action="store_true",
        help=("read available /sys/kernel/debug/usb/tcpm-*/log once; this "
              "advances/consumes buffered entries, and debugfs is never mounted"),
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    snapshot = collect_snapshot(
        args.sysfs_root,
        args.proc_root,
        label=args.label,
        include_commands=not args.no_commands,
        include_kernel_log=not args.no_kernel_log,
        include_tcpm_log=args.include_tcpm_log,
    )
    json.dump(snapshot, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
