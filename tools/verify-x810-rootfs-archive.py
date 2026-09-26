#!/usr/bin/env python3
"""Validate the rootfs tarball contract for installation to SM-X810 linuxroot."""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import tarfile
from pathlib import PurePosixPath


EXPECTED_PORT = {
    "schema_version": 1,
    "port_id": "x810-fedora",
    "device_id": "SM-X810",
    "os_id": "fedora",
    "arch": "aarch64",
}
PORT_METADATA = "usr/share/tab-companion/port.json"
MAX_METADATA_BYTES = 2 * 1024 * 1024


def fail(message: str) -> None:
    raise ValueError(message)


def archive_path(name: str) -> str:
    """Normalize a tar path and reject absolute/parent traversal names."""
    if name.startswith("/"):
        fail(f"absolute path in rootfs archive: {name!r}")
    parts = [part for part in PurePosixPath(name).parts if part not in ("", ".")]
    if ".." in parts:
        fail(f"parent traversal in rootfs archive path: {name!r}")
    return "/".join(parts)


def resolve_link(path: str, target: str) -> str:
    """Resolve a link target within the archive root, rejecting escape."""
    if target.startswith("/"):
        candidate = posixpath.normpath(target.lstrip("/"))
    else:
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if candidate == ".." or candidate.startswith("../"):
        fail(f"link escapes rootfs: {path!r} -> {target!r}")
    return candidate


def parse_manifest(path: str | None) -> dict[str, str]:
    if path is None:
        return {}
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in values:
                fail(f"duplicate rootfs manifest field: {key}")
            values[key] = value
    return values


def inspect(archive_pathname: str, manifest_path: str | None = None) -> dict[str, str]:
    manifest = parse_manifest(manifest_path)
    members: dict[str, tarfile.TarInfo] = {}
    metadata: dict[str, bytes] = {}
    with tarfile.open(archive_pathname, mode="r:gz") as archive:
        for member in archive:
            normalized = archive_path(member.name)
            if not normalized:
                continue
            if normalized in members:
                fail(f"duplicate path in rootfs archive: {normalized}")
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                fail(f"unsupported special file in rootfs archive: {normalized}")
            if member.issym():
                resolve_link(normalized, member.linkname)
            elif member.islnk():
                archive_path(member.linkname)
            members[normalized] = member
            if normalized in {
                "etc/fstab", "etc/os-release", "usr/lib/os-release", PORT_METADATA,
                "etc/passwd", "etc/shadow", "etc/hostname",
            }:
                if normalized == "etc/os-release" and member.issym():
                    continue
                if not member.isfile() or member.size > MAX_METADATA_BYTES:
                    fail(f"expected a small regular metadata file: {normalized}")
                source = archive.extractfile(member)
                if source is None:
                    fail(f"cannot read metadata file: {normalized}")
                metadata[normalized] = source.read(MAX_METADATA_BYTES + 1)
                if len(metadata[normalized]) > MAX_METADATA_BYTES:
                    fail(f"metadata file is too large: {normalized}")

    os_release_member = members.get("etc/os-release")
    if os_release_member is not None and os_release_member.issym():
        target = resolve_link("etc/os-release", os_release_member.linkname)
        if target not in metadata:
            fail("/etc/os-release symlink target is missing or not a regular file")
        metadata["etc/os-release"] = metadata[target]

    # Extractors must not follow an archive-created symlink while writing a
    # later member beneath it (e.g. /tmp escape during TWRP extraction).
    symlinks = {name for name, item in members.items() if item.issym()}
    for name in members:
        parts = name.split("/")
        if any("/".join(parts[:index]) in symlinks for index in range(1, len(parts))):
            fail(f"archive writes beneath a symlink: {name}")

    required = {
        "etc/fstab", "etc/os-release", PORT_METADATA,
        "etc/passwd", "etc/shadow", "etc/hostname",
    }
    missing = sorted(required - metadata.keys())
    if missing:
        fail("rootfs tarball missing required metadata: " + ", ".join(missing))

    fstab = metadata["etc/fstab"].decode("utf-8", errors="strict")
    entries = []
    for line in fstab.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 4:
            fail(f"malformed /etc/fstab line: {stripped}")
        entries.append(fields)
    root_entries = [entry for entry in entries if entry[1] == "/"]
    if len(root_entries) != 1 or root_entries[0][0] != "PARTLABEL=linuxroot" or root_entries[0][2] != "ext4":
        fail("/etc/fstab must mount ext4 root exactly once from PARTLABEL=linuxroot")
    if any(entry[1] == "/boot" for entry in entries):
        fail("X810 internal-UFS rootfs must not require a separate /boot partition")

    os_release = metadata["etc/os-release"].decode("utf-8", errors="strict")
    if not re.search(r"^ID=fedora\s*$", os_release, re.MULTILINE):
        fail("/etc/os-release does not identify Fedora")

    passwd = metadata["etc/passwd"].decode("utf-8", errors="strict")
    shadow = metadata["etc/shadow"].decode("utf-8", errors="strict")
    root_passwd = [line.split(":") for line in passwd.splitlines() if line.split(":", 1)[0] == "root"]
    root_shadow = [line.split(":") for line in shadow.splitlines() if line.split(":", 1)[0] == "root"]
    if len(root_passwd) != 1 or len(root_passwd[0]) < 7 or root_passwd[0][2] != "0":
        fail("root account is missing or malformed")
    if len(root_shadow) != 1 or len(root_shadow[0]) < 2 or not root_shadow[0][1].startswith(("!", "*")):
        fail("root password is not locked")

    nonlogin_shells = {"", "/sbin/nologin", "/usr/sbin/nologin", "/bin/false", "/usr/bin/false"}
    human_accounts = []
    for fields in (line.split(":") for line in passwd.splitlines() if line):
        if len(fields) < 7:
            fail("/etc/passwd contains a malformed entry")
        try:
            uid = int(fields[2])
        except ValueError:
            fail("/etc/passwd contains a non-numeric UID")
        if uid >= 1000 and fields[6] not in nonlogin_shells:
            human_accounts.append(fields[0])
    if human_accounts:
        fail("rootfs must not contain pre-created human accounts: " + ", ".join(human_accounts))
    hostname = metadata["etc/hostname"].decode("utf-8", errors="strict").strip()
    if hostname != "localhost.localdomain":
        fail("/etc/hostname must be neutral (localhost.localdomain)")

    try:
        port = json.loads(metadata[PORT_METADATA])
    except json.JSONDecodeError as error:
        fail(f"port.json is invalid JSON: {error}")
    if not isinstance(port, dict) or any(port.get(key) != value for key, value in EXPECTED_PORT.items()):
        fail("port.json schema/target does not match SM-X810 Fedora")
    if not isinstance(port.get("version"), str) or not port["version"]:
        fail("port.json has no version")

    module_paths = [name for name in members if name.startswith("usr/lib/modules/") and name.count("/") >= 3]
    if not module_paths:
        fail("rootfs archive contains no kernel module tree under /usr/lib/modules")
    for key, expected in (("device_id", "SM-X810"), ("os_id", "fedora"), ("arch", "aarch64")):
        if manifest and manifest.get(key) != expected:
            fail(f"rootfs manifest has wrong {key} for X810 Fedora")
    if manifest and manifest.get("port_version") not in (None, "unknown", port["version"]):
        fail("rootfs manifest version does not match port.json")

    return {
        "port_id": port["port_id"],
        "device_id": port["device_id"],
        "os_id": port["os_id"],
        "os_version": str(port.get("os_version", "")),
        "arch": port["arch"],
        "version": port["version"],
        "root": "PARTLABEL=linuxroot (ext4)",
        "kernel_modules": str(len(module_paths)),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="gts9wifi-fedora-*-rootfs.tar.gz")
    parser.add_argument("--manifest", help="matching rootfs-manifest.txt")
    args = parser.parse_args(argv)
    try:
        result = inspect(args.archive, args.manifest)
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as error:
        print(f"verify-x810-rootfs-archive: {error}", file=sys.stderr)
        return 1
    print("X810 rootfs archive OK: " + ", ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
