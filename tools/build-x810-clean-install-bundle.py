#!/usr/bin/env python3
"""Assemble a deterministic, verified SM-X810 Fedora clean-install bundle.

Inputs are the already-published kernel and rootfs release asset directories.
The bundle contains no vbmeta, recovery, private device data, or new firmware.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def load_tool(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().with_name(filename))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load required verifier {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_build_match = load_tool("verify-x810-build-match.py", "x810_build_match").verify
inspect_rootfs = load_tool("verify-x810-rootfs-archive.py", "x810_rootfs_archive").inspect


IMAGE_SIZES = {
    "boot": 100_663_296,
    "init_boot": 8_388_608,
    "vendor_boot": 100_663_296,
    "dtbo": 16_777_216,
}
VBMETA_SIZE = 131_072
MIN_ROOTFS_BYTES = 32 * 1024**3
ROOTFS_HEADROOM_FLOOR = 4 * 1024**3
SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, relative: str) -> dict[str, object]:
    return {"file": relative, "sha256": sha256(path), "size_bytes": path.stat().st_size}


def parse_kv_manifest(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key in result:
            raise ValueError(f"duplicate field {key!r} in {path.name}")
        result[key] = value
    return result


def verify_checksum_file(directory: Path, filename: str) -> dict[str, str]:
    checksum_path = directory / filename
    if not checksum_path.is_file():
        raise ValueError(f"missing {checksum_path}")
    entries: dict[str, str] = {}
    for line_number, line in enumerate(checksum_path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"invalid checksum line {line_number} in {filename}")
        digest, name = match.groups()
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
            raise ValueError(f"unsafe checksum path {name!r} in {filename}")
        if name in entries:
            raise ValueError(f"duplicate checksum entry {name!r} in {filename}")
        asset = directory / name
        if not asset.is_file() or sha256(asset) != digest:
            raise ValueError(f"checksum mismatch or missing asset: {filename}: {name}")
        entries[name] = digest
    if not entries:
        raise ValueError(f"empty checksum file: {filename}")
    return entries


def rootfs_content_bytes(archive_path: Path) -> int:
    """Logical bytes of regular files, used for a conservative partition floor."""
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            if member.isfile():
                total += member.size
    return total


def rootfs_kernel_release(archive_path: Path) -> str:
    """Require one installed module tree and return its uname -r directory."""
    releases: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            normalized = tuple(part for part in parts if part not in ("", "."))
            if len(normalized) >= 4 and normalized[:3] == ("usr", "lib", "modules"):
                releases.add(normalized[3])
    if len(releases) != 1:
        raise ValueError(f"rootfs must contain exactly one kernel module release directory; found {sorted(releases)}")
    release = next(iter(releases))
    if release in {"", ".", ".."} or "/" in release:
        raise ValueError("rootfs contains an invalid kernel module release directory")
    return release


def minimum_rootfs_size(
    content_bytes: int,
    minimum_floor: int = MIN_ROOTFS_BYTES,
    headroom_floor: int = ROOTFS_HEADROOM_FLOOR,
) -> int:
    """Admission threshold, not a promise of the free space after installation."""
    reserve = max(headroom_floor, (content_bytes * 20 + 99) // 100)
    return max(minimum_floor, content_bytes + reserve)


def safe_tar_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe archive member path: {name!r}")


def add_deterministic_file(tar: tarfile.TarFile, source: Path, name: str) -> None:
    safe_tar_name(name)
    info = tarfile.TarInfo(name)
    info.size = source.stat().st_size
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = tarfile.REGTYPE
    with source.open("rb") as stream:
        tar.addfile(info, stream)


def build_bundle(
    kernel_dir: Path,
    rootfs_dir: Path,
    output: Path,
    *,
    bundle_version: str,
    kernel_release: str,
    rootfs_release: str,
    image_sizes: dict[str, int] | None = None,
    minimum_rootfs_floor: int = MIN_ROOTFS_BYTES,
    rootfs_headroom_floor: int = ROOTFS_HEADROOM_FLOOR,
) -> dict[str, object]:
    if not SAFE_VERSION_RE.fullmatch(bundle_version):
        raise ValueError("bundle_version must be a safe, non-empty release tag")
    for label, value in (("kernel_release", kernel_release), ("rootfs_release", rootfs_release)):
        if not SAFE_VERSION_RE.fullmatch(value):
            raise ValueError(f"{label} must be a safe, non-empty release tag")

    expected_sizes = IMAGE_SIZES if image_sizes is None else image_sizes
    root_checksums = verify_checksum_file(rootfs_dir, "SHA256SUMS")
    kernel_image_checksums = verify_checksum_file(kernel_dir, "BUNDLE-SHA256SUMS")
    kernel_rpm_checksums = verify_checksum_file(kernel_dir, "RPM-SHA256SUMS")

    root_manifests = list(rootfs_dir.glob("rootfs-manifest.txt"))
    root_archives = sorted(rootfs_dir.glob("gts9wifi-fedora-*-rootfs.tar.gz"))
    kernel_rpms = sorted(kernel_dir.glob("linux-gts9wifi-*.rpm"))
    metadata_path = kernel_dir / "BUILD-METADATA.txt"
    if len(root_manifests) != 1 or len(root_archives) != 1:
        raise ValueError("rootfs release must contain exactly one rootfs-manifest.txt and rootfs tarball")
    if len(kernel_rpms) != 1 or not metadata_path.is_file():
        raise ValueError("kernel release must contain exactly one linux-gts9wifi RPM and BUILD-METADATA.txt")

    root_manifest_path = root_manifests[0]
    root_archive = root_archives[0]
    kernel_rpm = kernel_rpms[0]
    if root_archive.name not in root_checksums or root_manifest_path.name not in root_checksums:
        raise ValueError("rootfs SHA256SUMS does not cover both rootfs archive and manifest")
    if kernel_rpm.name not in kernel_rpm_checksums:
        raise ValueError("RPM-SHA256SUMS does not cover the kernel RPM")
    kernel_metadata = parse_kv_manifest(metadata_path)
    if not {"kernel_rpm_nevra", "kernel_rpm_sha256", "firmware_sha256"} <= kernel_metadata.keys():
        raise ValueError("kernel BUILD-METADATA.txt is missing required matched-build fields")
    if "BUILD-METADATA.txt" in kernel_image_checksums:
        raise ValueError("unexpected BUILD-METADATA.txt checksum in BUNDLE-SHA256SUMS")

    root_fields = parse_kv_manifest(root_manifest_path)
    if any(root_fields.get(key) != value for key, value in {
        "device_id": "SM-X810", "os_id": "fedora", "arch": "aarch64"
    }.items()):
        raise ValueError("rootfs manifest is not for SM-X810 Fedora aarch64")
    fedora_version = root_fields.get("os_version", "")
    if not fedora_version.isdigit():
        raise ValueError("rootfs manifest has no numeric Fedora version")
    inspect_rootfs(str(root_archive), str(root_manifest_path))
    verify_build_match(root_manifest_path, metadata_path, kernel_rpm)

    images: dict[str, Path] = {}
    for partition, expected_size in expected_sizes.items():
        source = kernel_dir / f"{partition}.img"
        if not source.is_file() or source.stat().st_size != expected_size:
            raise ValueError(f"missing or wrong-size {partition}.img (expected {expected_size} bytes)")
        if source.name not in kernel_image_checksums:
            raise ValueError(f"BUNDLE-SHA256SUMS does not cover {source.name}")
        images[partition] = source
    vbmeta = kernel_dir / "vbmeta.img"
    if vbmeta.exists():
        if vbmeta.stat().st_size != VBMETA_SIZE or "vbmeta.img" not in kernel_image_checksums:
            raise ValueError("source vbmeta image is not the known-sized, checksummed build output")
    # Refuse unrecognized image inputs instead of silently producing a partial set.
    listed_images = {name for name in kernel_image_checksums if name.endswith(".img")}
    if listed_images != {f"{name}.img" for name in expected_sizes} | ({"vbmeta.img"} if vbmeta.exists() else set()):
        raise ValueError("kernel checksum manifest has an unexpected or missing partition image")

    inventory_sources: dict[str, Path] = {
        f"rootfs/{root_archive.name}": root_archive,
        "rootfs/rootfs-manifest.txt": root_manifest_path,
        f"kernel/{kernel_rpm.name}": kernel_rpm,
        "kernel/BUILD-METADATA.txt": metadata_path,
    }
    for partition, image in images.items():
        inventory_sources[f"boot/{partition}.img"] = image
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=".x810-source-releases-", suffix=".tmp",
        dir=output.parent, delete=False,
    ) as temporary:
        release_metadata = Path(temporary.name)
        temporary.write(
            f"bundle_version={bundle_version}\nkernel_release={kernel_release}\nrootfs_release={rootfs_release}\n"
        )
    inventory_sources["metadata/SOURCE-RELEASES.txt"] = release_metadata
    temporary_output: Path | None = None
    try:
        records = {
            name: file_record(source, name)
            for name, source in sorted(inventory_sources.items())
        }
        content_bytes = rootfs_content_bytes(root_archive)
        min_rootfs = minimum_rootfs_size(content_bytes, minimum_rootfs_floor, rootfs_headroom_floor)
        rootfs_archive_record = records[f"rootfs/{root_archive.name}"]
        rootfs_manifest_record = records["rootfs/rootfs-manifest.txt"]
        kernel_rpm_record = records[f"kernel/{kernel_rpm.name}"]
        manifest: dict[str, object] = {
            "schema_version": 1,
            "type": "x810-clean-install",
            "device": {"model": "SM-X810", "codename": "gts9pwifi"},
            "os": {"id": "fedora", "version": fedora_version, "arch": "aarch64"},
            "bundle_version": bundle_version,
            "kernel": {"release": rootfs_kernel_release(root_archive), "rpm": kernel_rpm_record},
            "rootfs": {
                "archive": rootfs_archive_record,
                "manifest": rootfs_manifest_record,
                "filesystem": "ext4",
                "minimum_size_bytes": min_rootfs,
            },
            "boot_set": {"images": {key: records[f"boot/{key}.img"] for key in expected_sizes}},
            "files": records,
        }
        manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
        # mtime=0 and a blank gzip filename make identical inputs byte-identical.
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
        ) as temporary_archive:
            temporary_output = Path(temporary_archive.name)
            raw = temporary_archive
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    info = tarfile.TarInfo("x810-clean-install-manifest.json")
                    info.size = len(manifest_bytes)
                    info.mode = 0o644
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    import io

                    archive.addfile(info, io.BytesIO(manifest_bytes))
                    for name, source in sorted(inventory_sources.items()):
                        add_deterministic_file(archive, source, name)
        os.replace(temporary_output, output)
    finally:
        release_metadata.unlink(missing_ok=True)
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-dir", type=Path, required=True, help="downloaded kernel release assets")
    parser.add_argument("--rootfs-dir", type=Path, required=True, help="downloaded Fedora rootfs release assets")
    parser.add_argument("--output", type=Path, required=True, help="output combined .tar.gz installer asset")
    parser.add_argument("--bundle-version", required=True, help="combined release tag/version")
    parser.add_argument("--kernel-release", required=True, help="exact kernel source release tag")
    parser.add_argument("--rootfs-release", required=True, help="exact rootfs source release tag")
    args = parser.parse_args(argv)
    try:
        manifest = build_bundle(
            args.kernel_dir,
            args.rootfs_dir,
            args.output,
            bundle_version=args.bundle_version,
            kernel_release=args.kernel_release,
            rootfs_release=args.rootfs_release,
        )
    except (OSError, EOFError, tarfile.TarError, UnicodeError, ValueError) as error:
        parser.exit(1, f"build-x810-clean-install-bundle: REFUSING: {error}\n")
    print(
        f"Created {args.output} ({args.output.stat().st_size} bytes); "
        f"Fedora {manifest['os']['version']} X810, "
        f"linuxroot admission minimum {manifest['rootfs']['minimum_size_bytes']} bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
