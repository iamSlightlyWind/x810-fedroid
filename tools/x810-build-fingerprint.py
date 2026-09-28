#!/usr/bin/env python3
"""Calculate stable, input-based identities for X810 Fedora build components.

Only source paths that feed a component and explicit external inputs contribute
to its key. Git object content is read directly, so the same key can be
calculated for the current tree and for an older release's recorded commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


BUILDER_IMAGE = "quay.io/fedora/fedora:44"
LINUX_SOURCE_SHA256 = "f9fef3d14c0df53819026f4be74459835c2a0b0dcbf5b5bbd9ea19f0829402b3"
KVER = "7.2.0-gts9wifi"
# Bump the relevant revision when its reusable-workflow Docker commands,
# toolchain setup, build flags, or output assembly behavior changes. Cache-only
# changes do not require a bump.
KERNEL_RECIPE_REVISION = "2"
ROOTFS_RECIPE_REVISION = "3"
FULL_SET_RECIPE_REVISION = "1"

KERNEL_PATHS = (
    "kernel", "boot", "firmware/x810-cyg1",
    "tools/make-twrp-zip.py", "tools/x810-gpu-firmware.py",
)
ROOTFS_PATHS = (
    "rootfs",
    "firmware/x810-cyg1",
    "firmware/x810-vpu-cyg1",
    "specs",
    "tools/bdftool.py",
    "tools/stamp-port-metadata.py",
    "tools/build-port-support-rpm.sh",
    "tools/test-port-build-contract.py",
    "tools/verify-x810-rootfs-archive.py",
    "tools/x810-gpu-firmware.py",
)
FULL_SET_PATHS = (
    "tools/build-x810-clean-install-bundle.py",
    "tools/x810-release-manifest.py",
    "tools/verify-x810-build-match.py",
    "tools/verify-x810-rootfs-archive.py",
    "tools/x810-build-fingerprint.py",
    "tools/publish-x810-full-set.sh",
)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def _source_digest(root: Path, ref: str, paths: tuple[str, ...]) -> str:
    listing = _git(root, "ls-tree", "-r", "-z", ref, "--", *paths)
    digest = hashlib.sha256()
    entries = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", "surrogateescape")
        if kind != "blob":
            continue
        entries.append((path, mode, object_id))
    for path, mode, object_id in sorted(entries):
        payload = _git(root, "cat-file", "blob", object_id)
        digest.update(path.encode("utf-8", "surrogateescape"))
        digest.update(b"\0" + mode.encode("ascii") + b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def fingerprint(
    component: str,
    *,
    root: Path,
    ref: str,
    firmware_sha256: str,
    fedora_base_compose: str,
    fedora_updates_compose: str,
    fedora_release: str = "44",
    desktop: str = "gnome",
    port_version: str = "0.1.0",
    kernel_rpm_sha256: str = "",
    kernel_recipe_revision: str = KERNEL_RECIPE_REVISION,
    rootfs_recipe_revision: str = ROOTFS_RECIPE_REVISION,
    kernel_build_key: str = "",
    rootfs_build_key: str = "",
    full_set_recipe_revision: str = FULL_SET_RECIPE_REVISION,
) -> str:
    if component not in {"kernel", "rootfs", "full-set"}:
        raise ValueError("component must be kernel, rootfs, or full-set")
    if component in {"kernel", "rootfs"} and (
        not firmware_sha256 or not fedora_base_compose or not fedora_updates_compose
    ):
        raise ValueError("kernel/rootfs fingerprints require firmware and pinned Fedora composes")
    paths = {
        "kernel": KERNEL_PATHS,
        "rootfs": ROOTFS_PATHS,
        "full-set": FULL_SET_PATHS,
    }[component]
    parameters: dict[str, str] = {
        "schema": "x810-build-fingerprint-v1",
        "component": component,
    }
    if component == "kernel":
        parameters.update({
            "builder_image": BUILDER_IMAGE,
            "fedora_release": fedora_release,
            "fedora_base_compose": fedora_base_compose,
            "fedora_updates_compose": fedora_updates_compose,
            "firmware_sha256": firmware_sha256,
            "kver": KVER,
            "linux_source_sha256": LINUX_SOURCE_SHA256,
            "recipe_revision": kernel_recipe_revision,
        })
    elif component == "rootfs":
        parameters.update({
            "builder_image": BUILDER_IMAGE,
            "fedora_release": fedora_release,
            "fedora_base_compose": fedora_base_compose,
            "fedora_updates_compose": fedora_updates_compose,
            "firmware_sha256": firmware_sha256,
            "desktop": desktop,
            "port_version": port_version,
            "kernel_rpm_sha256": kernel_rpm_sha256,
            "recipe_revision": rootfs_recipe_revision,
        })
    else:
        parameters.update({
            "kernel_build_key": kernel_build_key,
            "rootfs_build_key": rootfs_build_key,
            "port_version": port_version,
            "recipe_revision": full_set_recipe_revision,
        })
    doc = {
        "parameters": parameters,
        "source_sha256": _source_digest(root, ref, paths),
    }
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("kernel", "rootfs", "full-set"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--firmware-sha256", default="")
    parser.add_argument("--fedora-base-compose", default="")
    parser.add_argument("--fedora-updates-compose", default="")
    parser.add_argument("--fedora-release", default="44")
    parser.add_argument("--desktop", default="gnome")
    parser.add_argument("--port-version", default="0.1.0")
    parser.add_argument("--kernel-rpm-sha256", default="")
    parser.add_argument("--kernel-build-key", default="")
    parser.add_argument("--rootfs-build-key", default="")
    args = parser.parse_args(argv)
    try:
        value = fingerprint(
            args.component,
            root=args.root.resolve(),
            ref=args.ref,
            firmware_sha256=args.firmware_sha256,
            fedora_base_compose=args.fedora_base_compose,
            fedora_updates_compose=args.fedora_updates_compose,
            fedora_release=args.fedora_release,
            desktop=args.desktop,
            port_version=args.port_version,
            kernel_rpm_sha256=args.kernel_rpm_sha256,
            kernel_build_key=args.kernel_build_key,
            rootfs_build_key=args.rootfs_build_key,
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(str(exc))
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
