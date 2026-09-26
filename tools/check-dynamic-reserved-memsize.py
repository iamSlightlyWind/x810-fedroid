#!/usr/bin/env python3
"""Compare Samsung /proc/memsize and meminfo snapshots with an X810 runtime DTB.

This checks pool names, sizes, flags and aggregate accounting. `%pK` may hide
physical addresses in the kernel report; no address comparison is attempted.
The X810 CYG1 kernel replaces `rbin` size with `expand_size` above 8 GiB.
Requires dtc's fdtget.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def fdtget(dtb: Path, *args: str) -> str | None:
    p = subprocess.run(["fdtget", str(dtb), *args], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p.stdout.strip() if p.returncode == 0 else None


def cells(dtb: Path, node: str, prop: str) -> list[int] | None:
    raw = fdtget(dtb, "-t", "x", node, prop)
    return [int(x, 16) for x in raw.split()] if raw else None


def integer(words: list[int] | None) -> int | None:
    if words is None:
        return None
    value = 0
    for word in words:
        value = (value << 32) | word
    return value


def dynamic_pools(dtb: Path) -> dict[str, tuple[int, bool, bool]]:
    root = "/reserved-memory"
    children = fdtget(dtb, "-l", root)
    if children is None:
        raise ValueError(f"missing {root} in {dtb}")
    pools = {}
    for child in children.splitlines():
        path = f"{root}/{child}"
        if fdtget(dtb, path, "reg") is not None:
            continue
        size = integer(cells(dtb, path, "size"))
        if size is None:
            continue
        status = fdtget(dtb, "-t", "s", path, "status") or "okay"
        props = set((fdtget(dtb, "-p", path) or "").split())
        actual_size = size
        if child.split("@")[0] == "rbin":
            expand = integer(cells(dtb, path, "expand_size"))
            if expand:
                actual_size = expand
        pools[child.split("@")[0]] = (
            0 if status == "disabled" else actual_size,
            "reusable" in props,
            "no-map" in props,
        )
    return pools


LINE = re.compile(
    r"^0x[0-9a-fA-F]+-0x[0-9a-fA-F]+\s+"
    r"(0x[0-9a-fA-F]+)\s+\(\s*\d+ KB\s*\)\s+"
    r"(nomap|map)\s+(reusable|unusable)\s+(\S+)\s*$"
)


def parse_memsize(path: Path) -> dict[str, tuple[int, bool, bool]]:
    pools = {}
    for line in path.read_text().splitlines():
        m = LINE.match(line)
        if not m:
            continue
        size, nomap, reusable, name = m.groups()
        pools[name] = (int(size, 16), reusable == "reusable", nomap == "nomap")
    return pools


def meminfo_kb(path: Path, field: str) -> int:
    pattern = re.compile(rf"^{re.escape(field)}:\s+(\d+) kB$", re.M)
    match = pattern.search(path.read_text())
    if not match:
        raise ValueError(f"{path}: missing {field}")
    return int(match.group(1))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dtb", type=Path)
    p.add_argument("memsize", type=Path, help="captured /proc/memsize/reserved")
    p.add_argument("meminfo", type=Path, help="captured /proc/meminfo")
    args = p.parse_args()
    for path in (args.dtb, args.memsize, args.meminfo):
        if not path.is_file():
            p.error(f"not a file: {path}")
    try:
        expected = {n: v for n, v in dynamic_pools(args.dtb).items() if v[0]}
        observed = parse_memsize(args.memsize)
        missing = sorted(set(expected) - set(observed))
        mismatches = []
        for name in sorted(set(expected) & set(observed)):
            if expected[name] != observed[name]:
                mismatches.append((name, expected[name], observed[name]))
        cma = sum(size for name, (size, reusable, nomap) in expected.items()
                  if name != "rbin" and reusable and not nomap)
        rbin = expected.get("rbin", (0, False, False))[0]
        cma_reported = meminfo_kb(args.meminfo, "CmaTotal") * 1024
        rbin_reported = meminfo_kb(args.meminfo, "RbinTotal") * 1024
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"active dynamic pools in DT: {len(expected)}; named in memsize: "
          f"{sum(n in observed for n in expected)}")
    for name in missing:
        print(f"MISSING pool report: {name}")
    for name, exp, got in mismatches:
        print(f"POOL MISMATCH {name}: DT={exp}, memsize={got}")
    print(f"CMA pool request sum={cma // 1024} KiB; meminfo CmaTotal={cma_reported // 1024} KiB")
    print(f"rbin effective request={rbin // 1024} KiB; meminfo RbinTotal={rbin_reported // 1024} KiB")
    if missing or mismatches or cma != cma_reported or rbin != rbin_reported:
        print("FAIL: dynamic pool accounting differs")
        return 1
    print("PASS: active pool names/sizes/flags and CMA/rbin totals reconcile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
