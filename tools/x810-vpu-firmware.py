#!/usr/bin/env python3
"""Hash-verify and stage the exact SM-X810 CYG1 VPU firmware for early boot."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tempfile


EXPECTED_SHA256 = "c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba"
FILENAME = "vpu30_4v.mbn"


def stage(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"VPU firmware source must be a regular non-symlink file: {source}")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise ValueError("VPU firmware does not match the exact SM-X810 CYG1 SHA-256")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / FILENAME
    fd, temporary = tempfile.mkstemp(prefix=f".{FILENAME}.", dir=destination)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage_parser = sub.add_parser("stage", help="verify and stage the exact CYG1 VPU image")
    stage_parser.add_argument("--source", type=Path, required=True)
    stage_parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()
    try:
        stage(args.source, args.dest)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"staged and SHA-256 verified SM-X810 CYG1 {FILENAME} into {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
