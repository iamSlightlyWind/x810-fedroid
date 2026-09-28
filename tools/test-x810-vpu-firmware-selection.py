#!/usr/bin/env python3
"""Guard against staging a sibling-model signed VPU image on SM-X810."""

from __future__ import annotations

import hashlib
import unittest
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "rootfs/stage-public-firmware.sh"
ASSET_STAGER = ROOT / "tools/stage-x810-vpu-firmware.sh"
X810_SHA256 = "c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba"
SIBLING_SHA256 = "431e976f95e3306ad9473e88c1c83795fce8de5811a4c7203c27e498f8aa3787"


class X810VpuFirmwareSelectionTests(unittest.TestCase):
    def test_stage_uses_only_owner_authorized_x810_firmware_hash(self) -> None:
        stage = STAGER.read_text()
        fetch = (ROOT / "rootfs/fetch-local-assets.sh").read_text()
        asset_stager = ASSET_STAGER.read_text()
        self.assertIn(X810_SHA256, stage)
        self.assertIn(X810_SHA256, asset_stager)
        self.assertIn('vpu_src="${GTS9_VPU_MBN:-$script_dir/../firmware/x810-vpu-cyg1/vpu30_4v.mbn}"', stage)
        self.assertIn('"${GTS9_VPU_MBN:-}"', fetch)
        self.assertIn("not the verified SM-X810 CYG1 image", stage)
        self.assertIn('if [ -n "${GTS9_VPU_MBN:-}" ]; then', fetch)
        self.assertIn('"$repo_dir/tools/stage-x810-vpu-firmware.sh" "$GTS9_VPU_MBN"', fetch)
        self.assertIn("local-assets/firmware-overrides", asset_stager)
        payload = ROOT / "firmware/x810-vpu-cyg1/vpu30_4v.mbn"
        self.assertTrue(payload.is_file(), "reproducible builds need the pinned X810 payload")
        self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(), X810_SHA256)
        rpm_builder = (ROOT / "tools/build-port-support-rpm.sh").read_text()
        self.assertIn("firmware/x810-vpu-cyg1/vpu30_4v.mbn", rpm_builder)
        self.assertIn('install -D -m0644 "$vpu_source" "$stage$vpu_path"', rpm_builder)
        self.assertIn("refusing unapproved boot/kernel/firmware path", rpm_builder)

    def test_public_sibling_blob_is_not_fetched_for_x810(self) -> None:
        for path in (STAGER, ROOT / "rootfs/fetch-local-assets.sh"):
            source = path.read_text()
            self.assertNotIn("raw.githubusercontent.com/Azkali/gts9wifi-firmware/main/qcom/sm8550/gts9wifi", source)
            self.assertNotIn("curl -fsSL --max-time 120 -o \"$vpu", source)
        self.assertIn(SIBLING_SHA256, STAGER.read_text())

    def test_clean_build_stages_checked_in_blob_and_rejects_wrong_override(self) -> None:
        with tempfile.TemporaryDirectory(prefix="x810-vpu-stage-test-") as temporary:
            root = Path(temporary) / "rootfs"
            (root / "usr/lib/firmware").mkdir(parents=True)
            env = os.environ.copy()
            env["GTS9_SKIP_PUBLIC_FIRMWARE"] = "1"
            env.pop("GTS9_VPU_MBN", None)
            subprocess.run([str(STAGER), str(root)], check=True, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            staged = root / "usr/lib/firmware/qcom/vpu/vpu30_4v.mbn"
            self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), X810_SHA256)

            env["GTS9_VPU_MBN"] = "/etc/hosts"
            bad_root = Path(temporary) / "bad-rootfs"
            (bad_root / "usr/lib/firmware").mkdir(parents=True)
            result = subprocess.run([str(STAGER), str(bad_root)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
