#!/usr/bin/env python3
"""Offline-check X810 stock CDSP MDT segments against their reserved ranges.

This only reads extracted firmware files. It does not stage firmware, touch a
device, write a DTB, or start remoteproc. Supply a directory containing
cdsp.mdt, cdsp_dtb.mdt and their referenced cdsp.bNN / cdsp_dtb.bNN payloads.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import tempfile


# These are the upstream SM8550 CDSP carveouts retained by the X810 DTB.
RANGES = {
    "cdsp": (0x9C900000, 0x02000000),
    "cdsp_dtb": (0x9E900000, 0x00080000),
}
ELF_MAGIC = b"\x7fELF"
PT_LOAD = 1
QCOM_AUTH_HASH_FLAGS = 2
# CYG1's primary CDSP MDT identifies the Hexagon image. The separately
# authenticated CDSP-DT MDT has e_machine=1 in the locked X810 stock payload.
# The Linux Qualcomm MDT loader parses the ELF32 program headers but does not
# dispatch on e_machine; keep the per-image observed values explicit rather
# than incorrectly treating the DT image as a second Hexagon executable.
EXPECTED_E_MACHINE = {
    "cdsp": 164,
    "cdsp_dtb": 1,
}


def load_segments(path: Path, expected_machine: int) -> list[tuple[int, int, int, int]]:
    """Return (segment_index, paddr, filesz, memsz) for loadable MDT entries."""
    data = path.read_bytes()
    if len(data) < 52 or data[:4] != ELF_MAGIC:
        raise ValueError(f"{path.name}: not a complete ELF MDT")
    if data[4:7] != b"\x01\x01\x01":
        raise ValueError(f"{path.name}: expected ELF32 little-endian MDT")

    # ELF32 header fields used here: e_type/e_machine, e_phoff, e_ehsize,
    # e_phentsize and e_phnum. Qualcomm's MDT program headers are 32 bytes.
    e_type = struct.unpack_from("<H", data, 16)[0]
    e_machine = struct.unpack_from("<H", data, 18)[0]
    e_version = struct.unpack_from("<I", data, 20)[0]
    phoff = struct.unpack_from("<I", data, 28)[0]
    ehsize, phentsize, phnum = struct.unpack_from("<HHH", data, 40)
    if e_type != 2:
        raise ValueError(f"{path.name}: expected an executable ELF MDT")
    if e_machine != expected_machine or e_version != 1:
        raise ValueError(
            f"{path.name}: expected ELF machine field {expected_machine} "
            f"and version 1, got {e_machine} and {e_version}"
        )
    if ehsize < 52 or phentsize != 32 or phnum == 0:
        raise ValueError(f"{path.name}: invalid MDT program-header geometry")
    if phoff < ehsize or phoff + phentsize * phnum > len(data):
        raise ValueError(f"{path.name}: truncated MDT program-header table")

    segments = []
    for index in range(phnum):
        entry = struct.unpack_from("<8I", data, phoff + index * phentsize)
        kind, _offset, _vaddr, paddr, filesz, memsz, flags, _align = entry
        # Qualcomm PAS authenticates these hash entries; they are metadata,
        # not memory segments to check against a Linux reserved-memory range.
        if kind != PT_LOAD or ((flags >> 24) & 7) == QCOM_AUTH_HASH_FLAGS:
            continue
        if memsz:
            if filesz > memsz:
                raise ValueError(
                    f"{path.name}: PT_LOAD {index} file size {filesz:#x} "
                    f"exceeds memory size {memsz:#x}"
                )
            segments.append((index, paddr, filesz, memsz))
    if not segments:
        raise ValueError(f"{path.name}: no loadable memory segments")
    return segments


def validate_image(directory: Path, name: str) -> int:
    start, size = RANGES[name]
    image = directory / f"{name}.mdt"
    if image.is_symlink():
        raise ValueError(f"{image.name}: symlink MDT is not accepted")
    segments = load_segments(image, EXPECTED_E_MACHINE[name])
    for index, paddr, filesz, memsz in segments:
        if paddr < start or paddr + memsz > start + size:
            raise ValueError(
                f"{image.name}: PT_LOAD {index} [{paddr:#x}, {paddr + memsz:#x}) "
                f"is outside reserved range [{start:#x}, {start + size:#x})"
            )
        if filesz:
            payload = directory / f"{name}.b{index:02d}"
            if payload.is_symlink():
                raise ValueError(f"{payload.name}: symlink payload is not accepted")
            if not payload.is_file():
                raise ValueError(f"{image.name}: missing payload {payload.name}")
            if payload.stat().st_size != filesz:
                raise ValueError(
                    f"{payload.name}: expected {filesz} bytes, got {payload.stat().st_size}"
                )
    return len(segments)


def _make_fixture(directory: Path, name: str, start: int, size: int) -> None:
    """Create a minimal one-segment MDT and corresponding payload for tests."""
    header = bytearray(52 + 32)
    header[:7] = ELF_MAGIC + b"\x01\x01\x01"
    struct.pack_into("<HH", header, 16, 2, EXPECTED_E_MACHINE[name])
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<I", header, 28, 52)
    struct.pack_into("<HHH", header, 40, 52, 32, 1)
    filesz = 4
    memsz = min(16, size)
    struct.pack_into("<8I", header, 52, PT_LOAD, 0, start, start,
                     filesz, memsz, 0, 4)
    (directory / f"{name}.mdt").write_bytes(header)
    (directory / f"{name}.b00").write_bytes(b"TEST")


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="x810-cdsp-layout-") as tmp:
        root = Path(tmp)
        for name, (start, size) in RANGES.items():
            _make_fixture(root, name, start, size)
            assert validate_image(root, name) == 1

        # Reject an image whose load would touch memory outside the carveout.
        _make_fixture(root, "cdsp", RANGES["cdsp"][0], RANGES["cdsp"][1])
        data = bytearray((root / "cdsp.mdt").read_bytes())
        struct.pack_into("<I", data, 52 + 12, sum(RANGES["cdsp"]))
        (root / "cdsp.mdt").write_bytes(data)
        try:
            validate_image(root, "cdsp")
        except ValueError as exc:
            assert "outside reserved range" in str(exc)
        else:
            raise AssertionError("out-of-range segment was accepted")

        # The one-past-end case is rejected, but the complete reservation
        # envelope itself is a valid boundary.
        _make_fixture(root, "cdsp", *RANGES["cdsp"])
        data = bytearray((root / "cdsp.mdt").read_bytes())
        struct.pack_into("<I", data, 52 + 20, RANGES["cdsp"][1] + 1)
        (root / "cdsp.mdt").write_bytes(data)
        try:
            validate_image(root, "cdsp")
        except ValueError as exc:
            assert "outside reserved range" in str(exc)
        else:
            raise AssertionError("one-byte-overrun segment was accepted")

        _make_fixture(root, "cdsp", *RANGES["cdsp"])
        data = bytearray((root / "cdsp.mdt").read_bytes())
        struct.pack_into("<H", data, 18, EXPECTED_E_MACHINE["cdsp_dtb"])
        (root / "cdsp.mdt").write_bytes(data)
        try:
            validate_image(root, "cdsp")
        except ValueError as exc:
            assert "expected ELF machine field 164" in str(exc)
        else:
            raise AssertionError("CDSP MDT with the DT image's machine field was accepted")

        # The signed DT image is not a Hexagon executable: X810 CYG1 records
        # machine field 1 while retaining ELF32 LE headers. It must pass the
        # same memory-boundary checks under its own expected header profile.
        _make_fixture(root, "cdsp_dtb", *RANGES["cdsp_dtb"])
        assert validate_image(root, "cdsp_dtb") == 1
        data = bytearray((root / "cdsp_dtb.mdt").read_bytes())
        struct.pack_into("<H", data, 18, EXPECTED_E_MACHINE["cdsp"])
        (root / "cdsp_dtb.mdt").write_bytes(data)
        try:
            validate_image(root, "cdsp_dtb")
        except ValueError as exc:
            assert "expected ELF machine field 1" in str(exc)
        else:
            raise AssertionError("CDSP-DT MDT with the CDSP machine field was accepted")

        _make_fixture(root, "cdsp", *RANGES["cdsp"])
        data = bytearray((root / "cdsp.mdt").read_bytes())
        struct.pack_into("<I", data, 52 + 16, 17)  # filesz > the 16-byte memsz.
        (root / "cdsp.mdt").write_bytes(data)
        try:
            validate_image(root, "cdsp")
        except ValueError as exc:
            assert "exceeds memory size" in str(exc)
        else:
            raise AssertionError("PT_LOAD filesz larger than memsz was accepted")

        # Restore a valid header, then reject a missing payload rather than
        # passing a partial extraction as a usable image.
        _make_fixture(root, "cdsp", *RANGES["cdsp"])
        (root / "cdsp.b00").unlink()
        try:
            validate_image(root, "cdsp")
        except ValueError as exc:
            assert "missing payload" in str(exc)
        else:
            raise AssertionError("missing payload was accepted")
    print("X810 CDSP MDT layout self-test passed (ELF contract, carveout bounds, segment files)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        help="directory of extracted cdsp*.mdt and cdsp*.bNN files")
    parser.add_argument("--self-test", action="store_true",
                        help="run the offline parser's synthetic safety tests")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    if args.directory:
        directory = args.directory.resolve(strict=True)
        counts = {name: validate_image(directory, name) for name in RANGES}
        print("X810 CDSP firmware load segments fit the retained reserved-memory ranges:")
        for name, count in counts.items():
            start, size = RANGES[name]
            print(f"  {name}: {count} segments in [{start:#x}, {start + size:#x})")
        print("This is an offline layout check only; it does not validate PAS boot, "
              "FastRPC transport, QNN runtime compatibility, or inference.")
    if not args.self_test and not args.directory:
        parser.error("provide --directory or --self-test")


if __name__ == "__main__":
    main()
