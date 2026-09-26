#!/usr/bin/env python3
"""Write the installed Tab Companion port-update build identity."""
import argparse
import json
import os
import re
import tempfile
from pathlib import Path


VERSION_RE = re.compile(r"(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})")
COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
BRANCH_RE = re.compile(r"[A-Za-z0-9_./-]{1,200}")


def write_build_info(path, *, version, run_id, run_number, commit, branch):
    if not VERSION_RE.fullmatch(version):
        raise ValueError("port version must be numeric MAJOR.MINOR.PATCH")
    if run_id < 1 or run_number < 1:
        raise ValueError("Actions run ID and run number must be positive")
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("commit must be a 40- or 64-character lowercase SHA")
    if not BRANCH_RE.fullmatch(branch) or ".." in branch.split("/"):
        raise ValueError("invalid build branch")

    document = {
        "schema_version": 1,
        "project": "x810-fedora",
        "version": version,
        "run_id": run_id,
        "run_number": run_number,
        "commit": commit,
        "branch": branch,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("version")
    parser.add_argument("run_id", type=int)
    parser.add_argument("run_number", type=int)
    parser.add_argument("commit")
    parser.add_argument("branch")
    args = parser.parse_args(argv)
    try:
        document = write_build_info(
            args.output, version=args.version, run_id=args.run_id,
            run_number=args.run_number, commit=args.commit, branch=args.branch,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Wrote port build metadata for run #{document['run_number']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
