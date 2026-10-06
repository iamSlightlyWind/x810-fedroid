#!/usr/bin/env python3
"""Keep the Fedora libcamera SRPM lock on an immutable Koji NVR URL."""

import json
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


def main():
    lock = json.loads((ROOT / "specs/libcamera-hi1337/fedora-44-libcamera.lock.json").read_text())
    expected_path = (
        f"/packages/{lock['name']}/{lock['version']}/{lock['release']}/src/"
        f"{lock['name']}-{lock['version']}-{lock['release']}.src.rpm"
    )
    parsed = urlparse(lock["srpm_url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "kojipkgs.fedoraproject.org"
    assert parsed.path == expected_path
    assert len(lock["srpm_sha256"]) == 64
    int(lock["srpm_sha256"], 16)
    print("X810 libcamera source-lock tests passed")


if __name__ == "__main__":
    main()
