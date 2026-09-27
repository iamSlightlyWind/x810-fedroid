#!/usr/bin/env python3
"""Create/read the compact aggregate release manifest used by X810 CI.

The release exposes this one JSON provenance/checksum file instead of several
standalone checksum and build-key assets. Component staging releases may keep
their legacy text files; this manifest lets later runs reconstruct and verify
those files after the aggregate has been pruned to one public release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = 1
MANIFEST_TYPE = "x810-fedora-release"
COMPONENT_FILES = {
    "kernel": {
        "key": "KERNEL-BUILD-KEY.txt",
        "text": ("BUILD-METADATA.txt", "BUNDLE-SHA256SUMS", "RPM-SHA256SUMS"),
    },
    "rootfs": {
        "key": "ROOTFS-BUILD-KEY.txt",
        "text": ("rootfs-manifest.txt", "SHA256SUMS"),
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_checksums(text: str, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"bad checksum entry at {label}:{number}")
        digest, name = match.groups()
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
            raise ValueError(f"unsafe checksum filename in {label}: {name!r}")
        if name in result:
            raise ValueError(f"duplicate checksum filename in {label}: {name!r}")
        result[name] = digest
    if not result:
        raise ValueError(f"empty checksum file: {label}")
    return result


def verify_component(directory: Path, component: dict[str, Any], label: str) -> None:
    for checksum_name, checksum_text in component["checksums"].items():
        entries = parse_checksums(checksum_text, f"{label}/{checksum_name}")
        for filename, expected in entries.items():
            path = directory / filename
            if not path.is_file() or sha256(path) != expected:
                raise ValueError(f"{label} asset checksum mismatch or missing file: {filename}")


def create(args: argparse.Namespace) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40,64}", args.source_commit):
        raise ValueError("source commit must be a full lowercase 40-64 character Git hash")
    kernel_dir: Path = args.kernel_dir
    rootfs_dir: Path = args.rootfs_dir
    components: dict[str, Any] = {}
    for name, directory in (("kernel", kernel_dir), ("rootfs", rootfs_dir)):
        fields = COMPONENT_FILES[name]
        key_path = directory / fields["key"]
        if not key_path.is_file():
            raise ValueError(f"missing {key_path}")
        checksum_names = [
            filename for filename in fields["text"]
            if filename.endswith("SHA256SUMS") or filename in {"BUNDLE-SHA256SUMS", "RPM-SHA256SUMS"}
        ]
        checksums: dict[str, str] = {}
        text_fields: dict[str, str] = {}
        for filename in fields["text"]:
            path = directory / filename
            if not path.is_file():
                raise ValueError(f"missing {path}")
            text = path.read_text(encoding="utf-8")
            if filename in checksum_names:
                checksums[filename] = text
            else:
                text_fields[filename] = text
        component = {
            "build_key": key_path.read_text(encoding="ascii").strip(),
            "release_tag": args.kernel_release if name == "kernel" else args.rootfs_release,
            "checksums": checksums,
            "text_files": text_fields,
        }
        if not component["build_key"]:
            raise ValueError(f"empty {fields['key']}")
        verify_component(directory, component, name)
        components[name] = component

    assets: list[dict[str, Any]] = []
    assets_by_name: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for source_name in args.asset:
        path = Path(source_name)
        if not path.is_file():
            raise ValueError(f"missing release payload asset: {path}")
        if path.name in seen:
            raise ValueError(f"duplicate release payload basename: {path.name}")
        seen.add(path.name)
        record = {"name": path.name, "sha256": sha256(path), "size_bytes": path.stat().st_size}
        assets.append(record)
        assets_by_name[path.name] = record

    kernel_names = {
        filename
        for checksum_text in components["kernel"]["checksums"].values()
        for filename in parse_checksums(checksum_text, "kernel checksums")
        if filename.endswith((".img", ".rpm"))
    }
    kernel_names.update(name for name in assets_by_name if name.startswith("x810-fedora-bootset-") and name.endswith(".zip"))
    rootfs_names = {
        filename
        for checksum_text in components["rootfs"]["checksums"].values()
        for filename in parse_checksums(checksum_text, "rootfs checksums")
        if filename.endswith((".tar.gz", ".rpm"))
    }
    for component_name, filenames in (("kernel", kernel_names), ("rootfs", rootfs_names)):
        absent = sorted(filenames - assets_by_name.keys())
        if absent:
            raise ValueError(f"{component_name} checksums reference unpublished payloads: {', '.join(absent)}")
        components[component_name]["asset_files"] = [assets_by_name[name] for name in sorted(filenames)]

    manifest = {
        "schema_version": SCHEMA,
        "type": MANIFEST_TYPE,
        "device": {"model": "SM-X810", "codename": "gts9pwifi"},
        "source_commit": args.source_commit,
        "release_tag": args.release_tag,
        "port_version": args.port_version,
        "build_keys": {
            "kernel": components["kernel"]["build_key"],
            "rootfs": components["rootfs"]["build_key"],
            "full_set": args.full_set_build_key,
        },
        "components": components,
        "assets": sorted(assets, key=lambda item: item["name"]),
    }
    if not manifest["build_keys"]["full_set"]:
        raise ValueError("full-set build key is required")
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA or manifest.get("type") != MANIFEST_TYPE:
        raise ValueError("unsupported X810 release manifest")
    if not isinstance(manifest.get("components"), dict):
        raise ValueError("release manifest has no component metadata")
    if not isinstance(manifest.get("build_keys"), dict):
        raise ValueError("release manifest has no build keys")
    return manifest


def materialize(path: Path, component_name: str, directory: Path, *, rpm_only: bool = False) -> None:
    manifest = load_manifest(path)
    components = manifest["components"]
    if component_name not in COMPONENT_FILES or not isinstance(components.get(component_name), dict):
        raise ValueError(f"release manifest has no {component_name!r} component")
    component = components[component_name]
    directory.mkdir(parents=True, exist_ok=True)
    fields = COMPONENT_FILES[component_name]
    if rpm_only and component_name != "kernel":
        raise ValueError("--rpm-only is supported only for the kernel component")
    if rpm_only:
        checksums = component.get("checksums", {})
        checksum_text = checksums.get("RPM-SHA256SUMS") if isinstance(checksums, dict) else None
        if not isinstance(checksum_text, str):
            raise ValueError("release manifest has no kernel RPM checksum")
        expected_text = {"RPM-SHA256SUMS": checksum_text}
        expected_names = {"RPM-SHA256SUMS"}
    else:
        expected_text = {fields["key"]: str(component.get("build_key", "")) + "\n"}
        text_files = component.get("text_files", {})
        checksum_files = component.get("checksums", {})
        if not isinstance(text_files, dict) or not isinstance(checksum_files, dict):
            raise ValueError(f"malformed {component_name} text/checksum metadata")
        expected_names = set(fields["text"]) | {fields["key"]}
        if set(text_files) | set(checksum_files) | {fields["key"]} != expected_names:
            raise ValueError(f"unexpected or missing {component_name} metadata filename")
        expected_text.update(text_files)
        expected_text.update(checksum_files)
    for filename, text in expected_text.items():
        if filename not in expected_names or not isinstance(text, str):
            raise ValueError(f"unsafe {component_name} metadata filename/value")
        existing = directory / filename
        if existing.exists():
            if existing.read_text(encoding="utf-8") != text:
                raise ValueError(f"downloaded {filename} conflicts with aggregate release manifest")
        else:
            existing.write_text(text, encoding="utf-8")
    verification_component = component
    if rpm_only:
        verification_component = {"checksums": {"RPM-SHA256SUMS": expected_text["RPM-SHA256SUMS"]}}
    verify_component(directory, verification_component, component_name)
    asset_records = component.get("asset_files", [])
    if rpm_only:
        asset_records = [record for record in asset_records if isinstance(record, dict) and str(record.get("name", "")).endswith(".rpm")]
    for record in asset_records:
        if not isinstance(record, dict):
            raise ValueError(f"malformed {component_name} payload record")
        filename = record.get("name")
        expected_hash = record.get("sha256")
        expected_size = record.get("size_bytes")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f"unsafe {component_name} payload filename")
        path = directory / filename
        if not path.is_file() or path.stat().st_size != expected_size or sha256(path) != expected_hash:
            raise ValueError(f"{component_name} payload SHA-256/size mismatch or missing: {filename}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--kernel-dir", type=Path, required=True)
    create_parser.add_argument("--rootfs-dir", type=Path, required=True)
    create_parser.add_argument("--source-commit", required=True)
    create_parser.add_argument("--release-tag", required=True)
    create_parser.add_argument("--kernel-release", required=True)
    create_parser.add_argument("--rootfs-release", required=True)
    create_parser.add_argument("--port-version", required=True)
    create_parser.add_argument("--full-set-build-key", required=True)
    create_parser.add_argument("--asset", action="append", default=[])
    create_parser.add_argument("--output", type=Path, required=True)

    key_parser = subparsers.add_parser("key")
    key_parser.add_argument("manifest", type=Path)
    key_parser.add_argument("component", choices=("kernel", "rootfs", "full_set"))

    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("manifest", type=Path)
    materialize_parser.add_argument("component", choices=("kernel", "rootfs"))
    materialize_parser.add_argument("directory", type=Path)
    materialize_parser.add_argument("--rpm-only", action="store_true", help="materialize/verify only the kernel RPM checksum")

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            manifest = create(args)
            args.output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        elif args.command == "key":
            manifest = load_manifest(args.manifest)
            key_name = args.component
            value = manifest.get("build_keys", {}).get(key_name, "")
            if not isinstance(value, str) or not value:
                raise ValueError(f"release manifest has no {key_name} build key")
            print(value)
        else:
            materialize(args.manifest, args.component, args.directory, rpm_only=args.rpm_only)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, f"x810-release-manifest: REFUSING: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
