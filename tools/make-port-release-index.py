#!/usr/bin/env python3
"""Create the legacy/full-set schema-1 port-release.json for one support RPM.

The combined full-set release continues to carry this index for installer
validation and compatibility. The in-OS Tab Companion updater consumes the
run-keyed public support-package release produced by the support job in
x810-fedora.yml.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse


PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,100}$")
VERSION_RE = re.compile(r"^(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,180}$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_@-]{0,127}$")
PACKAGE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:.+_~-]{0,127}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RELEASE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
MAX_ASSET_BYTES = 4 * 1024**3
TARGET_KEYS = ("os_id", "os_version", "arch", "device")
PORT_VERSION_FIELDS = {
    "port_id": "x810-fedora",
    "device_id": "SM-X810",
    "os_id": "fedora",
    "arch": "aarch64",
}
PORT_OS_VERSION = "44"


def fail(message):
    raise ValueError(message)


def parse_manifest(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in values:
            fail(f"duplicate manifest field: {key}")
        values[key] = value
    return values


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _target_matches(candidate, expected):
    if not isinstance(candidate, dict):
        return False
    for key in ("os_id", "arch"):
        if candidate.get(key) not in (expected.get(key), "*"):
            return False
    for key in ("os_version", "device"):
        if candidate.get(key, "*") not in (expected.get(key), "*"):
            return False
    return True


def validate_runtime_index(document, *, expected_project="x810-fedora", target=None):
    """Mirror the app parser's schema/asset/target checks for this one feed."""
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        fail("generated index is not schema version 1")
    if document.get("project") != expected_project:
        fail("generated index has the wrong project ID")
    version = document.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,80}", version):
        fail("generated index has an invalid app-compatible release version")
    assets = document.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= 100:
        fail("generated index has no valid assets")
    candidates = []
    for item in assets:
        if not isinstance(item, dict) or (target is not None and not _target_matches(item.get("target"), target)):
            continue
        if not isinstance(item.get("name"), str) or not NAME_RE.fullmatch(item["name"]):
            continue
        digest = item.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        if item.get("format") not in {"rpm", "deb", "pacman-local", "aur-source"}:
            continue
        if not isinstance(item.get("package_name"), str) or not PACKAGE_RE.fullmatch(item["package_name"]):
            continue
        if not isinstance(item.get("package_version"), str) or not PACKAGE_VERSION_RE.fullmatch(item["package_version"]):
            continue
        url = item.get("url")
        if not isinstance(url, str) or len(url) > 4096:
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            continue
        size = item.get("size")
        if not isinstance(size, int) or size < 1 or size > MAX_ASSET_BYTES:
            continue
        candidates.append(item)
    if not candidates:
        fail("generated index does not contain an app-runtime-compatible asset for the exact target")
    if target is not None and not any(item["target"] == target for item in candidates):
        fail("generated index did not select the exact Fedora/X810 target")
    return candidates[0]


def validate_port_metadata(path, version, repository, os_version):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read archived port.json: {exc}")
    expected = {
        "schema_version": 1,
        "port_id": "x810-fedora",
        "version": version,
        "device_id": "SM-X810",
        "os_id": "fedora",
        "os_version": os_version,
        "arch": "aarch64",
        "repo_url": f"https://github.com/{repository}",
    }
    if not isinstance(document, dict) or any(document.get(key) != value for key, value in expected.items()):
        fail("archived port.json has the wrong version, target, or canonical repository URL")
    if not isinstance(document.get("name"), str) or not document["name"].strip():
        fail("archived port.json is missing its port name")
    return document


def create_index(manifest_path, rpm_path, port_json_path, repository, release_tag):
    if not PROJECT_RE.fullmatch("x810-fedora"):
        fail("invalid port project ID")
    if not REPO_RE.fullmatch(repository):
        fail("repository must be owner/name")
    if not isinstance(release_tag, str) or not RELEASE_TAG_RE.fullmatch(release_tag):
        fail("combined release tag contains unsupported characters")

    manifest = parse_manifest(manifest_path)
    version = manifest.get("port_version", "")
    if not VERSION_RE.fullmatch(version):
        fail("port manifest must contain an explicit numeric release version")
    for key, expected in PORT_VERSION_FIELDS.items():
        if manifest.get(key) != expected:
            fail(f"port manifest has an unexpected {key}")
    package_name = manifest.get("port_package_name", "")
    package_version = manifest.get("port_package_version", "")
    package_arch = manifest.get("port_package_arch", "")
    asset_name = manifest.get("port_package_asset", "")
    if package_name != "x810-fedora-port" or package_arch != "noarch":
        fail("manifest does not identify the noarch x810-fedora-port package")
    if not PACKAGE_VERSION_RE.fullmatch(package_version) or not package_version.startswith(version + "-"):
        fail("support RPM package version does not match the port release version")
    if not NAME_RE.fullmatch(asset_name):
        fail("manifest contains an invalid RPM asset name")

    asset_path = Path(rpm_path)
    if not asset_path.is_file() or asset_path.is_symlink():
        fail(f"RPM asset is missing or not a regular file: {asset_path}")
    size = asset_path.stat().st_size
    if not 0 < size <= MAX_ASSET_BYTES:
        fail("RPM asset must be between 1 byte and 4 GiB")
    if asset_path.name != asset_name:
        fail("RPM asset filename does not match the rootfs manifest")
    try:
        rpm_fields = subprocess.check_output(
            ["rpm", "-qp", "--qf", "%{NAME}\\n%{VERSION}-%{RELEASE}\\n%{ARCH}", str(asset_path)],
            text=True,
        ).splitlines()
    except subprocess.CalledProcessError as exc:
        fail(f"cannot inspect support RPM metadata: {exc}")
    if rpm_fields != [package_name, package_version, package_arch]:
        fail("RPM name/version/architecture does not match its rootfs manifest entry")

    os_version = manifest.get("os_version", "")
    if not isinstance(os_version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,31}", os_version):
        fail("rootfs manifest has an invalid Fedora version")
    if os_version != PORT_OS_VERSION:
        fail(f"release updater target must be Fedora {PORT_OS_VERSION}")
    validate_port_metadata(port_json_path, version, repository, os_version)
    target = {
        "os_id": "fedora",
        "os_version": os_version,
        "arch": "aarch64",
        "device": "SM-X810",
    }
    if set(target) != set(TARGET_KEYS) or any(not isinstance(target[k], str) or not target[k] for k in TARGET_KEYS):
        fail("invalid update target")

    owner, repo = repository.split("/", 1)
    url = (
        f"https://github.com/{quote(owner, safe='')}/{quote(repo, safe='')}/releases/download/"
        f"{quote(release_tag, safe='')}/{quote(asset_name, safe='')}"
    )
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        fail("release asset URL must use HTTPS")

    document = {
        "schema_version": 1,
        "project": "x810-fedora",
        "version": version,
        "notes": "Fedora device-support package for SM-X810; kernel and boot updates remain manual TWRP operations.",
        "assets": [{
            "name": asset_name,
            "url": url,
            "sha256": sha256(asset_path),
            "size": size,
            "format": "rpm",
            "package_name": package_name,
            "package_version": package_version,
            "target": target,
        }],
    }
    validate_runtime_index(document, target=target)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="rootfs-manifest.txt from the matching rootfs build")
    parser.add_argument("--rpm", required=True, help="the manifest-listed support RPM")
    parser.add_argument("--port-json", required=True, help="port.json extracted from the matching rootfs tarball")
    parser.add_argument("--repository", required=True, help="GitHub owner/name for the X810 port repository")
    parser.add_argument("--tag", required=True, help="final combined release tag")
    parser.add_argument("--output", default="port-release.json")
    args = parser.parse_args(argv)
    try:
        document = create_index(args.manifest, args.rpm, args.port_json, args.repository, args.tag)
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, destination)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"make-port-release-index: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {args.output} for x810-fedora {document['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
