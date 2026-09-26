#!/usr/bin/env python3
"""Build an X810-only TWRP ZIP that stages/writes exactly one boot.img."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import stat
import zipfile
from pathlib import Path


PARTITION_SIZE = 100_663_296


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def zip_info(name: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def add_file(zf: zipfile.ZipFile, source: Path, arcname: str, mode: int = 0o644) -> None:
    with source.open("rb") as src, zf.open(zip_info(arcname, mode), "w", force_zip64=True) as dst:
        shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("boot_image", type=Path, help="partition-sized 100663296-byte boot.img")
    parser.add_argument("output", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--label", default="SM-X810 single boot image (manual TWRP test)")
    args = parser.parse_args()

    if not args.boot_image.is_file():
        raise SystemExit(f"missing boot image: {args.boot_image}")
    if args.boot_image.stat().st_size != PARTITION_SIZE:
        raise SystemExit(f"boot.img: expected {PARTITION_SIZE}, got {args.boot_image.stat().st_size}")
    update_binary = args.project / "tools/twrp/x810-boot-only-update-binary"
    updater_script = args.project / "tools/twrp/updater-script"
    if not update_binary.is_file() or not updater_script.is_file():
        raise SystemExit("TWRP installer sources are missing")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_file(zf, args.boot_image, "boot.img")
        add_file(zf, update_binary, "META-INF/com/google/android/update-binary", 0o755)
        add_file(zf, updater_script, "META-INF/com/google/android/updater-script")
        zf.writestr(zip_info("BUNDLE-LABEL"), args.label + "\n")
        zf.writestr(zip_info("SHA256SUMS"), f"{digest(args.boot_image)}  boot.img\n")

    print(f"{digest(args.output)}  {args.output.name}")


if __name__ == "__main__":
    main()
