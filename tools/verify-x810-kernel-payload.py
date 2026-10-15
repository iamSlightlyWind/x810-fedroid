#!/usr/bin/env python3
"""Verify the built X810 kernel contains the port's current pogo and PPS code."""

from __future__ import annotations

import argparse
import gzip
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kernel", type=Path, help="arm64 EFI zboot kernel image")
    parser.add_argument("pogo_source", type=Path, help="prepared pogo driver source")
    args = parser.parse_args()

    source = args.pogo_source.read_text(encoding="utf-8")
    revision = re.search(r"driver_revision=(\d+)", source)
    if not revision:
        raise SystemExit("cannot determine STM32 pogo driver revision from source")

    extractor = Path(__file__).resolve().parents[1] / "boot/extract-zboot-payload.py"
    with tempfile.TemporaryDirectory(prefix="x810-kernel-check-") as directory:
        payload = Path(directory) / "kernel.gz"
        subprocess.run(
            [sys.executable, str(extractor), str(args.kernel), str(payload)],
            check=True,
        )
        image = gzip.decompress(payload.read_bytes())

    required = (
        f"driver_revision={revision.group(1)}".encode(),
        b"same-point PPS keep-alive completed without parking pump",
        b"Handling deferred source Alert after PPS AMS",
    )
    for marker in required:
        if marker not in image:
            raise SystemExit(f"built kernel is missing required marker: {marker.decode()}")
    print(
        "Verified built X810 kernel contains "
        f"STM32 pogo driver revision {revision.group(1)} and PPS fixes"
    )


if __name__ == "__main__":
    main()
