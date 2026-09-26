#!/usr/bin/env python3
"""Build the public, Tab Companion-compatible X810 port updater bundle."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path


PROJECT = "x810-fedora"
PORT_VERSION_RE = re.compile(r"(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,180}")
MAX_PACKAGE_SIZE = 4 * 1024**3


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_bundle(rpm_path, build_info_path, output_path):
    rpm_path = Path(rpm_path)
    build_info_path = Path(build_info_path)
    output_path = Path(output_path)
    if not rpm_path.is_file() or rpm_path.is_symlink():
        raise ValueError("support RPM must be a regular file")
    if not build_info_path.is_file() or build_info_path.is_symlink():
        raise ValueError("port build metadata must be a regular file")
    info = json.loads(build_info_path.read_text(encoding="utf-8"))
    if not isinstance(info, dict) or info.get("schema_version") != 1 or info.get("project") != PROJECT:
        raise ValueError("invalid port build metadata identity")
    version = info.get("version")
    run_id, run_number = info.get("run_id"), info.get("run_number")
    commit, branch = info.get("commit"), info.get("branch")
    if not isinstance(version, str) or not PORT_VERSION_RE.fullmatch(version):
        raise ValueError("port build metadata has an invalid numeric version")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (run_id, run_number)):
        raise ValueError("port build metadata has invalid Actions run identity")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("port build metadata has an invalid commit SHA")
    if not isinstance(branch, str) or not branch or len(branch) > 200:
        raise ValueError("port build metadata has an invalid branch")

    try:
        fields = subprocess.check_output(
            ["rpm", "-qp", "--qf", "%{NAME}\n%{VERSION}\n%{RELEASE}\n%{ARCH}", str(rpm_path)],
            text=True,
        ).splitlines()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot inspect support RPM: {exc}") from exc
    if len(fields) != 4:
        raise ValueError("rpm returned incomplete support package metadata")
    package_name, package_version, package_release, arch = fields
    if package_name != "x810-fedora-port" or package_version != version or arch != "noarch":
        raise ValueError("support RPM name, version, or architecture does not match port metadata")
    if not re.fullmatch(rf"1000000\.{run_number}(?:\.[A-Za-z0-9._+~-]+)?", package_release):
        raise ValueError("support RPM release does not uniquely increase for this Actions run")

    size = rpm_path.stat().st_size
    if not 0 < size <= MAX_PACKAGE_SIZE:
        raise ValueError("support RPM is empty or too large")
    if not SAFE_NAME_RE.fullmatch(rpm_path.name):
        raise ValueError("support RPM filename is unsafe for an update bundle")
    document = {
        "schema_version": 1,
        "project": PROJECT,
        "version": f"{version}+{run_number}",
        "run_id": run_id,
        "run_number": run_number,
        "commit": commit,
        "branch": branch,
        "assets": [{
            "name": rpm_path.name,
            "sha256": sha256(rpm_path),
            "size": size,
            "format": "rpm",
            "package_name": package_name,
            "package_version": f"{package_version}-{package_release}",
            "target": {
                "os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810",
            },
        }],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("tab-companion-update.json", json.dumps(document, indent=2) + "\n")
            archive.write(rpm_path, rpm_path.name)
        os.replace(temporary, output_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpm", required=True)
    parser.add_argument("--build-info", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        document = create_bundle(args.rpm, args.build_info, args.output)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Wrote updater bundle for x810-fedora build #{document['run_number']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
