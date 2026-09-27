#!/usr/bin/env python3
"""Ensure a clean GNOME image has its intended camera app and backend."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "rootfs/build-rootfs.sh"


def main() -> None:
    builder = BUILDER.read_text(encoding="utf-8")
    assert "'@^workstation-product-environment' snapshot" in builder
    assert "pipewire-plugin-libcamera" in builder
    assert "libcamera-ipa" in builder
    print("PASS: clean GNOME images install Snapshot and its libcamera/PipeWire backend")


if __name__ == "__main__":
    main()
