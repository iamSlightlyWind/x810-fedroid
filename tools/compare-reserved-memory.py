#!/usr/bin/env python3
"""Audit candidate DT fixed reservations against a measured reference FDT.

Requires dtc's fdtget utility. Compares address/size tuples, not node labels,
because labels are not present in flattened runtime device trees. It also
requires exact root RAM tuple and `no-map` parity. This is an audit helper,
not a claim that matching trees are sufficient to boot safely.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, order=True)
class Region:
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


def fdtget(dtb: Path, *args: str) -> str | None:
    proc = subprocess.run(
        ["fdtget", str(dtb), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def cell_count(dtb: Path, node: str, prop: str, default: int) -> int:
    value = fdtget(dtb, "-t", "x", node, prop)
    if value is None:
        return default
    try:
        count = int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{dtb}: invalid {node}/{prop}: {value!r}") from exc
    if not 1 <= count <= 4:
        raise ValueError(f"{dtb}: unsupported cell count {count} at {node}/{prop}")
    return count


def cells_to_int(cells: list[int]) -> int:
    value = 0
    for cell in cells:
        value = (value << 32) | cell
    return value


def parse_reg(dtb: Path, node: str, address_cells: int, size_cells: int) -> list[Region]:
    raw = fdtget(dtb, "-t", "x", node, "reg")
    if raw is None:
        return []
    cells = [int(cell, 16) for cell in raw.split()]
    tuple_cells = address_cells + size_cells
    if len(cells) % tuple_cells:
        raise ValueError(
            f"{dtb}: {node}/reg has {len(cells)} cells, not a multiple of {tuple_cells}"
        )
    regions = []
    for offset in range(0, len(cells), tuple_cells):
        entry = cells[offset : offset + tuple_cells]
        start = cells_to_int(entry[:address_cells])
        size = cells_to_int(entry[address_cells:])
        if size:
            regions.append(Region(start, size))
    return regions


def reserved_regions(dtb: Path) -> tuple[dict[Region, tuple[str, bool]], int, int]:
    node = "/reserved-memory"
    address_cells = cell_count(dtb, node, "#address-cells", 2)
    size_cells = cell_count(dtb, node, "#size-cells", 1)
    children = fdtget(dtb, "-l", node)
    if children is None:
        raise ValueError(f"{dtb}: missing {node}")
    result: dict[Region, tuple[str, bool]] = {}
    for child in children.splitlines():
        path = f"{node}/{child}"
        props = set((fdtget(dtb, "-p", path) or "").split())
        for region in parse_reg(dtb, path, address_cells, size_cells):
            if region in result:
                raise ValueError(f"{dtb}: duplicate reserved range {region}")
            result[region] = (child, "no-map" in props)
    return result, address_cells, size_cells


def memory_regions(dtb: Path, *, allow_empty: bool = False) -> list[Region]:
    address_cells = cell_count(dtb, "/", "#address-cells", 2)
    size_cells = cell_count(dtb, "/", "#size-cells", 1)
    children = fdtget(dtb, "-l", "/")
    if children is None:
        raise ValueError(f"{dtb}: cannot list root nodes")
    regions = []
    for child in children.splitlines():
        path = "/" + child
        device_type = fdtget(dtb, "-t", "s", path, "device_type")
        if device_type == "memory" or child.startswith("memory@"):
            regions.extend(parse_reg(dtb, path, address_cells, size_cells))
    if not regions and not allow_empty:
        raise ValueError(f"{dtb}: no root memory ranges found")
    return regions


def overlaps(a: Region, b: Region) -> bool:
    return a.start < b.end and b.start < a.end


def fmt(region: Region) -> str:
    return f"{region.start:#x} + {region.size:#x} (end {region.end - 1:#x})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="measured runtime reference DTB")
    parser.add_argument("candidate", type=Path, help="candidate Linux DTB")
    args = parser.parse_args()
    for path in (args.reference, args.candidate):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    try:
        reference, _, _ = reserved_regions(args.reference)
        candidate, _, _ = reserved_regions(args.candidate)
        ram = memory_regions(args.reference)
        candidate_ram = memory_regions(args.candidate, allow_empty=True)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    missing = sorted(set(reference) - set(candidate))
    extras = sorted(set(candidate) - set(reference))
    no_map_mismatch = [
        region
        for region in sorted(set(reference) & set(candidate))
        if reference[region][1] != candidate[region][1]
    ]
    extra_in_ram = [region for region in extras if any(overlaps(region, mem) for mem in ram)]
    ram_missing = sorted(set(ram) - set(candidate_ram))
    ram_extra = sorted(set(candidate_ram) - set(ram))
    candidate_overlaps = [
        (left, right)
        for index, left in enumerate(sorted(candidate))
        for right in sorted(candidate)[index + 1 :]
        if overlaps(left, right)
    ]

    print(
        f"reference: {len(reference)} fixed reserved ranges; "
        f"candidate: {len(candidate)}; reference RAM ranges: {len(ram)}, "
        f"candidate RAM ranges: {len(candidate_ram)}"
    )
    for region in missing:
        name, no_map = reference[region]
        print(f"MISSING {'no-map ' if no_map else ''}{name}: {fmt(region)}")
    if not candidate_ram:
        print("CANDIDATE HAS NO USABLE RAM RANGES (possibly an ABL-patched zero-size placeholder)")
    for region in ram_missing:
        print(f"RAM RANGE MISSING FROM CANDIDATE: {fmt(region)}")
    for region in ram_extra:
        print(f"CANDIDATE-ONLY RAM RANGE: {fmt(region)}")
    for region in no_map_mismatch:
        reference_flag = "yes" if reference[region][1] else "no"
        candidate_flag = "yes" if candidate[region][1] else "no"
        print(
            f"NO-MAP MISMATCH {candidate[region][0]}: {fmt(region)} "
            f"(measured={reference_flag}, candidate={candidate_flag})"
        )
    for region in extras:
        name, no_map = candidate[region]
        ram_note = " overlaps measured RAM" if region in extra_in_ram else ""
        print(f"EXTRA {'no-map ' if no_map else ''}{name}: {fmt(region)}{ram_note}")

    for left, right in candidate_overlaps:
        print(
            f"OVERLAP {candidate[left][0]} {fmt(left)} / "
            f"{candidate[right][0]} {fmt(right)}"
        )

    if missing or no_map_mismatch or extra_in_ram or candidate_overlaps or ram_missing or ram_extra:
        print("FAIL: reservation audit requires explicit reconciliation")
        return 1
    print("PASS: measured RAM and fixed ranges plus exact no-map semantics are preserved")
    if extras:
        print("NOTE: candidate extras outside measured RAM still require provenance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
