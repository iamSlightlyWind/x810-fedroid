#!/usr/bin/env python3
"""Fail closed unless rootfs and boot releases use identical X810 build inputs."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def fields(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key in result:
            raise ValueError(f"duplicate field {key} in {path}")
        result[key] = value
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_sha(value: str, label: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} is missing or is not a lowercase SHA-256 digest")
    return value


def verify(rootfs_manifest: Path, kernel_metadata: Path, kernel_rpm: Path) -> None:
    root = fields(rootfs_manifest)
    kernel = fields(kernel_metadata)

    root_rpm_sha = require_sha(root.get("kernel_rpm_sha256", ""), "rootfs kernel_rpm_sha256")
    kernel_rpm_digest = require_sha(kernel.get("kernel_rpm_sha256", ""), "kernel bundle kernel_rpm_sha256")
    actual_rpm_sha = sha256(kernel_rpm)
    if root_rpm_sha != kernel_rpm_digest or root_rpm_sha != actual_rpm_sha:
        raise ValueError("rootfs and boot set do not use the exact same kernel RPM bytes")

    root_nevra = root.get("kernel_rpm_nevra", "")
    kernel_nevra = kernel.get("kernel_rpm_nevra", "")
    if not root_nevra or root_nevra != kernel_nevra:
        raise ValueError("rootfs kernel RPM NEVRA does not match kernel bundle metadata")

    root_firmware_sha = require_sha(root.get("firmware_sha256", ""), "rootfs firmware_sha256")
    kernel_firmware_sha = require_sha(kernel.get("firmware_sha256", ""), "kernel bundle firmware_sha256")
    if root_firmware_sha != kernel_firmware_sha:
        raise ValueError("rootfs and boot bundle do not use the same firmware archive")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs-manifest", required=True, type=Path)
    parser.add_argument("--kernel-metadata", required=True, type=Path)
    parser.add_argument("--kernel-rpm", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        verify(args.rootfs_manifest, args.kernel_metadata, args.kernel_rpm)
    except (OSError, ValueError) as error:
        parser.exit(1, f"verify-x810-build-match: {error}\n")
    print("Rootfs and boot build inputs match: exact kernel RPM and firmware SHA-256 verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
