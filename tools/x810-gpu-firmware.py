#!/usr/bin/env python3
"""Stage and verify the SM-X810 CYG1 Adreno 740 firmware set.

Samsung's signed CYG1 GPU blobs are not the same as Fedora's generic SM8550
firmware.  The generic files let the kernel probe the GPU but TrustZone rejects
the ZAP image, so the Adreno driver returns -EINVAL and GNOME's accelerated
session cannot start. The owner confirmed redistribution rights; the exact
firmware set is tracked under firmware/x810-cyg1 and is embedded in the built
rootfs and vendor_boot image.

The expected SHA-256 values below were captured from the owner-supplied
SM-X810 X810XXS5CYG1 firmware extraction.  Only these exact blobs are staged.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import struct
import subprocess
import tarfile
import tempfile
from typing import Mapping


EXPECTED_SHA256 = {
    "a740_sqe.fw": "96fee336424b139100fc60b5b45a907360e4b3936d7e1d00406b9bd80ca48473",
    "a740_zap.mdt": "f26566cedfac842f04d774649620289d126485dab67a6701cad4b6149586a94e",
    "a740_zap.b00": "2fb7b6afa4387a8ba81e1faf0c35ac1c6fd6294118426d5b45946d75f4e3e339",
    "a740_zap.b01": "21c0afb7418fe901d688327bbc7423898cc3d6ca93d77adff8294c6fca30fbcd",
    "a740_zap.b02": "b17a3fa6e323fcdc8cfde727220d469b05cae9b616e3dcaeea4766efc404e248",
    "gmu_gen70200.bin": "1a2a419c39046d3141fc5fed5aa7f971de2db40cc7a1d89693c3e26fad64dd98",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _directory_candidates(source: Path, name: str) -> list[Path]:
    common = (
        source / name,
        source / "qcom" / name,
        source / "usr/lib/firmware/qcom" / name,
        source / "vendor-extract/firmware" / name,
    )
    found = [p for p in common if p.is_file() and not p.is_symlink()]
    if not found and source.is_dir():
        found = [p for p in source.rglob(name) if p.is_file() and not p.is_symlink()]
    return found


def read_firmware_set(source: Path, expected: Mapping[str, str] = EXPECTED_SHA256) -> dict[str, bytes]:
    """Read only the named blobs and reject any non-CYG1 content."""
    result: dict[str, bytes] = {}
    if source.is_dir():
        for name, wanted_hash in expected.items():
            matching = []
            for path in _directory_candidates(source, name):
                data = path.read_bytes()
                if _sha256(data) == wanted_hash:
                    matching.append(data)
            if not matching:
                raise ValueError(f"missing or wrong X810 CYG1 firmware: {name}")
            if any(item != matching[0] for item in matching[1:]):
                raise ValueError(f"ambiguous sources for X810 firmware: {name}")
            result[name] = matching[0]
        return result

    if not source.is_file():
        raise ValueError(f"firmware source does not exist: {source}")
    try:
        archive = tarfile.open(source, "r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"firmware source must be a directory or tar archive: {source}") from exc
    if source.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("firmware archive exceeds the 4 MiB input limit")
    with archive:
        members = archive.getmembers()
        if len(members) > 128:
            raise ValueError("firmware archive has too many members")
        for name, wanted_hash in expected.items():
            candidates = [member for member in members
                          if member.isfile() and Path(member.name).name == name]
            matching: list[bytes] = []
            for member in candidates:
                if member.size > 1024 * 1024:
                    raise ValueError(f"firmware archive member is too large: {member.name}")
                stream = archive.extractfile(member)
                if stream is None:
                    continue
                data = stream.read()
                if _sha256(data) == wanted_hash:
                    matching.append(data)
            if not matching:
                raise ValueError(f"archive missing or has wrong X810 CYG1 firmware: {name}")
            if any(item != matching[0] for item in matching[1:]):
                raise ValueError(f"ambiguous archive entries for X810 firmware: {name}")
            result[name] = matching[0]
    return result


def stage(source: Path, destination: Path, expected: Mapping[str, str] = EXPECTED_SHA256) -> None:
    blobs = read_firmware_set(source, expected)
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in blobs.items():
        target = destination / name
        fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=destination)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _parse_newc(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    offset = 0
    while offset + 110 <= len(data):
        header = data[offset : offset + 110]
        if header[:6] not in (b"070701", b"070702"):
            raise ValueError(f"invalid newc CPIO header at byte {offset}")
        fields = [int(header[6 + i * 8 : 14 + i * 8], 16) for i in range(13)]
        file_size, name_size = fields[6], fields[11]
        name_start = offset + 110
        name_end = name_start + name_size
        if name_size < 1 or name_end > len(data):
            raise ValueError("invalid newc CPIO filename length")
        raw_name = data[name_start : name_end - 1]
        name = raw_name.decode("utf-8", "surrogateescape").lstrip("./")
        body_start = (name_end + 3) & ~3
        body_end = body_start + file_size
        if body_end > len(data):
            raise ValueError(f"truncated newc CPIO member: {name}")
        if name == "TRAILER!!!":
            break
        if stat.S_ISREG(fields[1]) and (
            name.startswith("usr/lib/firmware/qcom/")
            or name.startswith("lib/firmware/qcom/")
        ):
            files[name.rsplit("/", 1)[-1]] = data[body_start:body_end]
        offset = (body_end + 3) & ~3
    return files


def check_vendor_boot(image: Path, lz4: str = "lz4", expected: Mapping[str, str] = EXPECTED_SHA256) -> None:
    raw = image.read_bytes()
    if len(raw) < 4096 or raw[:8] != b"VNDRBOOT":
        raise ValueError("not an Android vendor_boot image")
    version, page_size = struct.unpack_from("<II", raw, 8)
    ramdisk_size = struct.unpack_from("<I", raw, 24)[0]
    if version not in (3, 4) or page_size < 512 or page_size & (page_size - 1):
        raise ValueError("unsupported vendor_boot header")
    start, end = page_size, page_size + ramdisk_size
    if end > len(raw) or ramdisk_size == 0:
        raise ValueError("vendor_boot ramdisk bounds are invalid")
    with tempfile.NamedTemporaryFile(prefix="x810-ramdisk-", suffix=".lz4") as blob:
        blob.write(raw[start:end])
        blob.flush()
        completed = subprocess.run(
            [lz4, "-d", "-c", blob.name], check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    if completed.returncode:
        raise ValueError(f"cannot decompress vendor_boot ramdisk with {lz4}: {completed.stderr.decode(errors='replace').strip()}")
    files = _parse_newc(completed.stdout)
    missing = sorted(set(expected) - files.keys())
    if missing:
        raise ValueError("vendor_boot initramfs lacks X810 CYG1 GPU firmware: " + ", ".join(missing))
    wrong = [name for name, digest in expected.items() if _sha256(files[name]) != digest]
    if wrong:
        raise ValueError("vendor_boot has generic/wrong GPU firmware instead of X810 CYG1 blobs: " + ", ".join(wrong))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage_parser = sub.add_parser("stage", help="verify and stage exact CYG1 blobs")
    stage_parser.add_argument("--source", type=Path, required=True, help="extracted firmware dir or tar.gz")
    stage_parser.add_argument("--dest", type=Path, required=True, help="qcom firmware destination")
    verify_parser = sub.add_parser("verify-source", help="verify the exact source blobs without writing")
    verify_parser.add_argument("--source", type=Path, required=True, help="extracted firmware dir or tar.gz")
    check_parser = sub.add_parser("check-vendor-boot", help="verify the firmware embedded in vendor_boot")
    check_parser.add_argument("image", type=Path)
    check_parser.add_argument("--lz4", default="lz4")
    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            stage(args.source, args.dest)
            print(f"staged and SHA-256 verified {len(EXPECTED_SHA256)} X810 CYG1 GPU firmware blobs into {args.dest}")
        elif args.command == "verify-source":
            read_firmware_set(args.source)
            print(f"verified {len(EXPECTED_SHA256)} X810 CYG1 GPU firmware blobs in {args.source}")
        else:
            check_vendor_boot(args.image, args.lz4)
            print(f"verified X810 CYG1 Adreno firmware in {args.image}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
