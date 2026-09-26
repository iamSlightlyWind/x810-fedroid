#!/usr/bin/env python3
"""Stamp Tab Companion's local X810/Fedora identity into a rootfs tree.

The overlay supplies a conservative `unknown` version for developer builds.
Only an explicit numeric dotted release version may replace it; build/release
tags are intentionally not inputs to this helper.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path


UNKNOWN_VERSION = "unknown"
VERSION_RE = re.compile(r"^(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})$")
OS_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
PORT_PATH = Path("usr/share/tab-companion/port.json")


def validate_version(version):
    if version == UNKNOWN_VERSION:
        return
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("port version must be 'unknown' or numeric MAJOR.MINOR.PATCH")


def validate_os_version(os_version):
    if not isinstance(os_version, str) or not OS_VERSION_RE.fullmatch(os_version):
        raise ValueError("Fedora release must be a short alphanumeric version token")


def stamp_port_metadata(rootfs, version, os_version):
    validate_version(version)
    validate_os_version(os_version)

    path = Path(rootfs) / PORT_PATH
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing regular port metadata file: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int \
            or document["schema_version"] != 1:
        raise ValueError("port metadata must use schema_version 1")
    expected = {
        "port_id": "x810-fedora",
        "device_id": "SM-X810",
        "os_id": "fedora",
        "arch": "aarch64",
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise ValueError(f"port metadata has unexpected {key}")
    for key in ("name", "repo_url"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise ValueError(f"port metadata needs a non-empty {key}")

    document["version"] = version
    document["os_version"] = os_version
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".port.json.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rootfs", help="rootfs staging directory")
    parser.add_argument("version", help="explicit numeric port release version, or 'unknown'")
    parser.add_argument("os_version", help="Fedora release identifier, e.g. 44")
    args = parser.parse_args(argv)
    try:
        document = stamp_port_metadata(args.rootfs, args.version, args.os_version)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"stamp-port-metadata: {exc}", file=sys.stderr)
        return 1
    print(document["version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
