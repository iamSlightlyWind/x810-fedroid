#!/usr/bin/env python3
"""Stage owner-supplied X810 CYG1 CDSP firmware into an ignored local cache.

This is extraction/validation only: it neither enables CDSP nor adds these
proprietary files to rootfs builds, releases, or the Git worktree.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "specs/x810-npu/x810-cyg1-cdsp.sha256"
LAYOUT_CHECK = ROOT / "tools/verify-x810-cdsp-firmware-layout.py"
LOCAL_ASSETS = ROOT / "local-assets"
NON_HLOS_SHA256 = "5b4f13cdd65f5887dfb876028e6b3e7aec99bdc2ceac5491b0991ea7d5b885e6"
DSPSO_SHA256 = "0ddffc8598858d597107644e712ca7a8e14baac32e045b2ac04f85c4c608ac86"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path = MANIFEST) -> dict[str, str]:
    entries: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2 or len(fields[0]) != 64:
            raise ValueError(f"{path}:{number}: expected SHA256 and relative path")
        digest, name = fields
        if any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"{path}:{number}: invalid lowercase SHA256")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{path}:{number}: unsafe path {name!r}")
        if name in entries:
            raise ValueError(f"{path}:{number}: duplicate path {name!r}")
        entries[name] = digest
    return entries


def verify_extracted(extraction: Path, expected: dict[str, str]) -> None:
    actual: dict[str, Path] = {}
    for path in (extraction / "cdsp").rglob("*"):
        if path.is_symlink():
            raise ValueError(f"unexpected symlink in firmware extraction: {path}")
        if path.is_file():
            actual["cdsp/" + path.relative_to(extraction / "cdsp").as_posix()] = path
    for path in (extraction / "dsp" / "cdsp").rglob("*"):
        if path.is_symlink():
            raise ValueError(f"unexpected symlink in firmware extraction: {path}")
        if path.is_file():
            actual["dsp/cdsp/" + path.relative_to(extraction / "dsp" / "cdsp").as_posix()] = path

    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise ValueError("firmware file-set mismatch (" + "; ".join(details) + ")")
    for name, want in expected.items():
        got = sha256(actual[name])
        if got != want:
            raise ValueError(f"SHA256 mismatch for {name}: got {got}, expected {want}")


def ensure_source(path: Path, label: str, expected: str) -> Path:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")
    got = sha256(path)
    if got != expected:
        raise ValueError(f"{label} SHA256 mismatch: got {got}, expected {expected}")
    return path


def checked_output(output: Path) -> tuple[Path, Path]:
    # local-assets is intentionally ignored, but do not follow a user-created
    # directory symlink and silently stage proprietary material elsewhere.
    if LOCAL_ASSETS.is_symlink():
        raise ValueError(f"ignored local-assets path must not be a symlink: {LOCAL_ASSETS}")
    assets_root = LOCAL_ASSETS.resolve()
    output = output.absolute()
    parent = output.parent.resolve()
    if parent != assets_root and assets_root not in parent.parents:
        raise ValueError(f"output must be below ignored {assets_root}; refusing to stage blobs elsewhere")
    output = parent / output.name
    if output.exists():
        raise ValueError(f"output already exists; refusing to overwrite: {output}")
    parent.mkdir(parents=True, exist_ok=True)
    return parent, output


def publish_tree(extracted: Path, output: Path) -> None:
    """Validate an exact already-extracted tree, then publish only under local-assets."""
    expected = load_manifest()
    if len(expected) != 43:
        raise ValueError(f"expected the locked 43-file manifest, found {len(expected)} entries")
    extracted = extracted.resolve(strict=True)
    parent, output = checked_output(output)
    verify_extracted(extracted, expected)
    cdsp = extracted / "cdsp"
    subprocess.run(
        [sys.executable, str(LAYOUT_CHECK), "--directory", str(cdsp)],
        check=True,
    )

    with tempfile.TemporaryDirectory(prefix="x810-cdsp-", dir=parent) as temporary:
        staged = Path(temporary) / "staged"
        shutil.copytree(cdsp, staged / "usr/lib/firmware/qcom/sm8550")
        shutil.copytree(
            extracted / "dsp" / "cdsp",
            staged / "usr/share/qcom/sm8550/Samsung/gts9pwifi/cdsp",
        )
        os.rename(staged, output)

    print(f"Verified and staged {len(expected)} owner-supplied X810 CYG1 files in {output}")
    print("This local cache is not consumed by default rootfs/release builds and does not enable CDSP.")


def stage(non_hlos: Path, dspso: Path, output: Path) -> None:
    non_hlos = ensure_source(non_hlos, "NON-HLOS.bin", NON_HLOS_SHA256)
    dspso = ensure_source(dspso, "dspso.bin", DSPSO_SHA256)
    expected = load_manifest()
    if len(expected) != 43:
        raise ValueError(f"expected the locked 43-file manifest, found {len(expected)} entries")

    parent, output = checked_output(output)

    for command in ("mcopy", "debugfs"):
        if shutil.which(command) is None:
            raise ValueError(f"missing extraction tool: {command}")

    with tempfile.TemporaryDirectory(prefix="x810-cdsp-", dir=parent) as temporary:
        work = Path(temporary)
        extracted = work / "extracted"
        (extracted / "cdsp").mkdir(parents=True)
        (extracted / "dsp").mkdir()
        subprocess.run(
            ["mcopy", "-i", str(non_hlos), "::/image/cdsp*", str(extracted / "cdsp")],
            check=True,
        )
        subprocess.run(
            ["debugfs", "-R", f"rdump /cdsp {extracted / 'dsp'}", str(dspso)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        verify_extracted(extracted, expected)
        subprocess.run(
            [sys.executable, str(LAYOUT_CHECK), "--directory", str(extracted / "cdsp")],
            check=True,
        )
        staged = work / "staged"
        shutil.copytree(extracted / "cdsp", staged / "usr/lib/firmware/qcom/sm8550")
        shutil.copytree(extracted / "dsp" / "cdsp", staged / "usr/share/qcom/sm8550/Samsung/gts9pwifi/cdsp")
        # Publish only into ignored cache after source-image, file-lock, and
        # layout checks all pass.
        os.rename(staged, output)

    print(f"Verified and staged {len(expected)} owner-supplied X810 CYG1 files in {output}")
    print("This local cache is not consumed by default rootfs/release builds and does not enable CDSP.")


def self_test() -> None:
    """Test strict file-set/hash validation with synthetic, non-proprietary data."""
    with tempfile.TemporaryDirectory(prefix="x810-cdsp-stage-test-") as temporary:
        root = Path(temporary)
        (root / "cdsp").mkdir()
        (root / "dsp/cdsp").mkdir(parents=True)
        files = {
            "cdsp/test.mdt": b"synthetic mdt",
            "dsp/cdsp/test.so": b"synthetic skel",
        }
        expected: dict[str, str] = {}
        for name, content in files.items():
            rel = Path(name)
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            expected[name] = hashlib.sha256(content).hexdigest()
        verify_extracted(root, expected)

        (root / "dsp/cdsp/test.so").write_bytes(b"changed")
        try:
            verify_extracted(root, expected)
        except ValueError as exc:
            assert "SHA256 mismatch" in str(exc)
        else:
            raise AssertionError("modified firmware was accepted")

        (root / "dsp/cdsp/test.so").write_bytes(files["dsp/cdsp/test.so"])
        (root / "cdsp/extra.bin").write_bytes(b"unexpected")
        try:
            verify_extracted(root, expected)
        except ValueError as exc:
            assert "unexpected" in str(exc)
        else:
            raise AssertionError("extra firmware file was accepted")
    print("X810 CDSP staging self-test passed (synthetic files only)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--non-hlos", type=Path, help="decompressed X810 CYG1 BL NON-HLOS.bin")
    parser.add_argument("--dspso", type=Path, help="decompressed X810 CYG1 BL dspso.bin")
    parser.add_argument(
        "--verified-tree", type=Path,
        help="already-extracted tree with cdsp/ and dsp/cdsp/ files; all 43 locked hashes are checked",
    )
    parser.add_argument("--output", type=Path, help="new destination under local-assets/ (ignored)")
    parser.add_argument("--self-test", action="store_true", help="test strict file-set/hash checks")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.verified_tree:
        if args.non_hlos or args.dspso or not args.output:
            parser.error("--verified-tree requires --output and cannot be combined with --non-hlos/--dspso")
        try:
            publish_tree(args.verified_tree, args.output)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                print(exc.stderr, file=sys.stderr, end="")
            parser.exit(1, f"{parser.prog}: error: {exc}\n")
        return
    if not args.non_hlos or not args.dspso or not args.output:
        parser.error("--non-hlos, --dspso, and --output are required unless --self-test is used")
    try:
        stage(args.non_hlos, args.dspso, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr, end="")
        parser.exit(1, f"{parser.prog}: error: {exc}\n")


if __name__ == "__main__":
    main()
