#!/usr/bin/env python3
"""Inventory active and disabled dynamic /reserved-memory nodes in a DTB.

This deliberately reports allocation requests, not guessed physical placements.
It is an audit aid: ABL and Linux may allocate pools differently.
Requires dtc's fdtget.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def get(dtb: Path, *args: str) -> str | None:
    p = subprocess.run(["fdtget", str(dtb), *args], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p.stdout.strip() if p.returncode == 0 else None


def cells(dtb: Path, node: str, prop: str) -> list[int] | None:
    raw = get(dtb, "-t", "x", node, prop)
    return [int(x, 16) for x in raw.split()] if raw else None


def as_int(words: list[int] | None) -> int | None:
    if words is None:
        return None
    value = 0
    for word in words:
        value = (value << 32) | word
    return value


def walk(dtb: Path, path: str):
    yield path
    children = get(dtb, "-l", path)
    if children:
        for name in children.splitlines():
            yield from walk(dtb, path.rstrip("/") + "/" + name)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dtb", type=Path)
    args = p.parse_args()
    if not args.dtb.is_file():
        p.error(f"not a file: {args.dtb}")
    root = "/reserved-memory"
    ac = as_int(cells(args.dtb, root, "#address-cells")) or 2
    sc = as_int(cells(args.dtb, root, "#size-cells")) or 1
    children = get(args.dtb, "-l", root)
    if children is None:
        print(f"ERROR: {args.dtb}: missing {root}", file=sys.stderr)
        return 2
    dynamic = []
    for child in children.splitlines():
        node = f"{root}/{child}"
        if get(args.dtb, node, "reg") is not None:
            continue
        requested = cells(args.dtb, node, "size")
        if requested is None:
            continue
        status = get(args.dtb, "-t", "s", node, "status") or "okay (implicit)"
        props = set((get(args.dtb, "-p", node) or "").split())
        size = as_int(requested)
        align = as_int(cells(args.dtb, node, "alignment"))
        expand = as_int(cells(args.dtb, node, "expand_size"))
        alloc = cells(args.dtb, node, "alloc-ranges")
        dynamic.append((node, status, size, align, expand, alloc,
                        "reusable" in props, "no-map" in props))
    active_sum = sum(row[2] or 0 for row in dynamic if row[1] != "disabled")
    print(f"{args.dtb}: {len(dynamic)} dynamic pools; active base-size sum={active_sum:#x} ({active_sum // (1024*1024)} MiB)")
    for node, status, size, align, expand, alloc, reusable, nomap in dynamic:
        fields = [f"status={status}", f"size={size:#x}" if size is not None else "size=?"]
        if align is not None: fields.append(f"alignment={align:#x}")
        if expand is not None: fields.append(f"expand_size={expand:#x}")
        if alloc is not None: fields.append("alloc-ranges=" + ",".join(f"{x:#x}" for x in alloc))
        fields += [f"reusable={reusable}", f"no-map={nomap}"]
        print(f"{node}: " + " ".join(fields))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
