#!/usr/bin/env python3
"""Distro-neutral host/recovery primitives shared by the X810 installers.

The byte-identical copy in x810-btwdroid is guarded by the installer parity
test. Keep OS policy, manifests, and destructive-install plans in each port.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Check:
    label: str
    state: str
    detail: str


def command(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)


def output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout + "\n" + result.stderr).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def adb_devices(adb_output: str) -> list[tuple[str, str]]:
    """Return all actionable ADB serial/state pairs from `adb devices -l`."""
    states = {"device", "recovery", "sideload", "unauthorized", "offline"}
    result: list[tuple[str, str]] = []
    for line in adb_output.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] in states:
            result.append((fields[0], fields[1]))
    return result


def adb_wait_hint(adb_output: str, serial: str | None = None) -> str:
    rows = adb_devices(adb_output)
    if serial:
        rows = [row for row in rows if row[0] == serial]
        if not rows:
            return f"{serial} is not listed; check USB, then run `adb devices -l` again"
    if any(state == "unauthorized" for _, state in rows):
        return "unlock Android and approve the USB-debugging RSA prompt, or open TWRP and reconnect; rerun `doctor`"
    if any(state == "offline" for _, state in rows):
        return "reconnect USB, then run `adb kill-server` and `adb start-server`; rerun `doctor`"
    if not rows:
        return "connect USB; enable USB debugging in Android or open TWRP; rerun `doctor`"
    return "no usable ADB target; run `adb devices -l` and resolve its state before continuing"


def adb_target(adb: str, serial: str | None,
               runner: Callable[..., subprocess.CompletedProcess[str]] = command,
               allowed_states: set[str] | None = None) -> tuple[str, str]:
    """Select exactly one ADB target and enforce the caller's state policy."""
    allowed_states = {"recovery"} if allowed_states is None else allowed_states
    result = runner([adb, "devices", "-l"], timeout=15)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "adb devices failed")
    devices = adb_devices(result.stdout)
    if serial:
        selected = [item for item in devices if item[0] == serial]
        if len(selected) != 1:
            raise RuntimeError(adb_wait_hint(result.stdout, serial))
    else:
        if not devices:
            raise RuntimeError(adb_wait_hint(result.stdout))
        if len(devices) != 1:
            raise RuntimeError("multiple ADB targets attached; pass --serial SERIAL")
        selected = devices
    target, state = selected[0]
    if state not in allowed_states:
        if state == "unauthorized":
            raise RuntimeError("ADB target is unauthorized; approve its USB-debugging RSA prompt and retry")
        if state == "offline":
            raise RuntimeError("ADB target is offline; reconnect USB and retry")
        if allowed_states == {"recovery"}:
            detail = f"expected TWRP ADB recovery, got {state}"
        elif allowed_states == {"device"}:
            detail = f"expected Android ADB device, got {state}"
        else:
            expected = " or ".join(sorted(allowed_states))
            detail = f"expected ADB state {expected}, got {state}"
        raise RuntimeError(detail)
    return target, state


def adb_binary(adb: str, serial: str, remote_command: str, timeout: int = 45) -> bytes:
    """Read device bytes via `adb exec-out` without text decoding."""
    try:
        result = subprocess.run(
            [adb, "-s", serial, "exec-out", "sh", "-c", remote_command],
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"ADB binary read failed: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or f"adb exec-out exited {result.returncode}")
    return result.stdout


def stream_adb_file(adb: str, serial: str, source: Path, remote_command: str,
                    timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    """Send a local artifact on stdin to a shell command through adb exec-in."""
    try:
        with source.open("rb") as stream:
            return subprocess.run(
                [adb, "-s", serial, "exec-in", remote_command],
                stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout, check=False,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"ADB stream failed: {error}") from error


class ADBClient:
    """Injectable text/binary ADB transport shared by both installer flows."""

    def __init__(self, adb: str, serial: str,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = command,
                 binary_reader: Callable[..., bytes] = adb_binary,
                 stream_runner: Callable[..., subprocess.CompletedProcess[str]] = stream_adb_file):
        self.adb = adb
        self.serial = serial
        self.runner = runner
        self.binary_reader = binary_reader or adb_binary
        self.stream_runner = stream_runner or stream_adb_file

    def run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return self.runner([self.adb, "-s", self.serial, *args], timeout=timeout)

    def shell(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return self.run("shell", *args, timeout=timeout)

    def push(self, source: Path, destination: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return self.run("push", str(source), destination, timeout=timeout)

    def pull(self, source: str, destination: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return self.run("pull", source, str(destination), timeout=timeout)

    def read_binary(self, remote_command: str, timeout: int = 180) -> bytes:
        return self.binary_reader(self.adb, self.serial, remote_command, timeout=timeout)

    def stream(self, source: Path, remote_command: str,
               timeout: int = 1800) -> subprocess.CompletedProcess[str]:
        return self.stream_runner(self.adb, self.serial, source, remote_command, timeout=timeout)


def parse_sgdisk_info(text: str) -> dict[str, str] | None:
    """Parse stable identity/extent fields from `sgdisk --info=N`."""
    if "does not exist" in text.lower():
        return None
    fields = {
        "type_guid": r"^Partition GUID code:\s*([0-9A-Fa-f-]+)",
        "unique_guid": r"^Partition unique GUID:\s*([0-9A-Fa-f-]+)",
        "first": r"^First sector:\s*(\d+)",
        "last": r"^Last sector:\s*(\d+)",
        "name": r"^Partition name:\s*'(.*)'\s*$",
    }
    parsed: dict[str, str] = {}
    for key, pattern in fields.items():
        match = re.search(pattern, text, flags=re.MULTILINE)
        if not match:
            return None
        parsed[key] = match.group(1)
    parsed["type_guid"] = parsed["type_guid"].upper()
    parsed["unique_guid"] = parsed["unique_guid"].upper()
    return parsed


def hash_password(password: str) -> str:
    """Hash through OpenSSL stdin; the plaintext never enters argv or a file."""
    if not password or "\n" in password or "\r" in password or "\x00" in password:
        raise ValueError("password must be non-empty and contain no newline or NUL")
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin"], input=password + "\n",
            text=True, capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"could not hash password with OpenSSL: {error}") from error
    hashed = result.stdout.strip()
    if result.returncode != 0 or not hashed.startswith("$6$"):
        raise ValueError("OpenSSL did not produce a SHA-512-crypt password hash")
    return hashed


def read_twrp_properties(client: ADBClient) -> dict[str, str]:
    """Collect identity/security properties without applying a distro policy."""
    values: dict[str, str] = {}
    for key, prop in (
        ("twrp", "ro.twrp.version"),
        ("device", "ro.product.device"),
        ("model", "ro.boot.em.model"),
        ("bootloader", "ro.bootloader"),
        ("flash_locked", "ro.boot.flash.locked"),
        ("vbmeta_state", "ro.boot.vbmeta.device_state"),
        ("verifiedbootstate", "ro.boot.verifiedbootstate"),
    ):
        result = client.shell("getprop", prop)
        values[key] = result.stdout.strip().replace("\r", "") if result.returncode == 0 else ""
    uid = client.shell("id", "-u")
    values["uid"] = uid.stdout.strip() if uid.returncode == 0 else ""
    cmdline = client.shell("cat", "/proc/cmdline")
    values["cmdline"] = cmdline.stdout.strip() if cmdline.returncode == 0 else ""
    return values


def twrp_identity(client: ADBClient, state: str, *, expected_model: str,
                  codenames: set[str], bootloader_prefix: str,
                  model_aliases: set[str] | None = None,
                  require_bootloader: bool = False,
                  allow_cmdline_identity: bool = False) -> tuple[dict[str, str], list[Check]]:
    """Shared TWRP/unlock/root checks parameterized by explicit device policy."""
    values = read_twrp_properties(client)
    aliases = model_aliases or set()
    checks = [Check("ADB target", "OK", f"{client.serial} ({state})")]
    if state == "recovery":
        checks.append(Check("Recovery state", "OK", "TWRP ADB recovery"))
    else:
        checks.append(Check("Recovery state", "STOP", f"expected TWRP recovery, got ADB state {state}"))
    if values["twrp"]:
        checks.append(Check("TWRP", "OK", values["twrp"]))
    else:
        checks.append(Check("TWRP", "STOP", "ro.twrp.version is empty"))
    if values["device"] in codenames:
        checks.append(Check("Recovery codename", "OK", values["device"]))
    else:
        checks.append(Check("Recovery codename", "STOP", f"expected {'/'.join(sorted(codenames))}, got {values['device'] or '?'}"))

    locked = values["flash_locked"] == "1" or values["vbmeta_state"].lower() == "locked"
    unlock_evidence = []
    if values["flash_locked"] == "0":
        unlock_evidence.append("ro.boot.flash.locked=0")
    if values["vbmeta_state"].lower() == "unlocked":
        unlock_evidence.append("vbmeta device_state=unlocked")
    if values["verifiedbootstate"].lower() == "orange":
        unlock_evidence.append("verifiedbootstate=orange")
    if locked:
        checks.append(Check("Bootloader", "STOP", "TWRP boot properties report a locked bootloader"))
    elif unlock_evidence:
        checks.append(Check("Bootloader", "OK", ", ".join(unlock_evidence)))
    else:
        checks.append(Check("Bootloader", "STOP", "TWRP boot properties do not prove the bootloader is unlocked"))

    model = values["model"]
    bootloader = values["bootloader"]
    bootloader_matches = bootloader.startswith(bootloader_prefix)
    cmdline_matches = f"androidboot.em.model={expected_model}" in values["cmdline"]
    known_model = model == expected_model or model in aliases
    invalid_model = bool(model and not known_model)
    invalid_bootloader = bool(bootloader and not bootloader_matches)
    identity_model_ok = known_model or (require_bootloader and not model)
    positive_identity = identity_model_ok or bootloader_matches or (allow_cmdline_identity and cmdline_matches)
    if require_bootloader:
        positive_identity = identity_model_ok and bootloader_matches
    if invalid_model or invalid_bootloader or not positive_identity:
        checks.append(Check(
            "Tablet identity", "STOP",
            f"requires {expected_model} identity and {bootloader_prefix} bootloader; got "
            f"{model or '?'} / {bootloader or '?'} / {values['device'] or '?'}",
        ))
    else:
        detail = bootloader or model or expected_model
        if model in aliases:
            detail += f"; generic TWRP model alias {model} accepted only with {bootloader_prefix} bootloader/codename"
        checks.append(Check("Tablet identity", "OK", detail))
    if values["uid"] == "0":
        checks.append(Check("Recovery privileges", "OK", "ADB shell is root"))
    else:
        checks.append(Check("Recovery privileges", "STOP", f"ADB shell uid is {values['uid'] or 'unknown'}, expected 0"))
    return values, checks

