#!/usr/bin/env python3
"""Guard against staging a sibling-model signed VPU image on SM-X810."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGER = ROOT / "rootfs/stage-public-firmware.sh"
ASSET_STAGER = ROOT / "tools/stage-x810-vpu-firmware.sh"
X810_SHA256 = "c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba"
SIBLING_SHA256 = "431e976f95e3306ad9473e88c1c83795fce8de5811a4c7203c27e498f8aa3787"


class X810VpuFirmwareSelectionTests(unittest.TestCase):
    def test_stage_requires_owner_supplied_x810_firmware_hash(self) -> None:
        stage = STAGER.read_text()
        fetch = (ROOT / "rootfs/fetch-local-assets.sh").read_text()
        asset_stager = ASSET_STAGER.read_text()
        self.assertIn(X810_SHA256, stage)
        self.assertIn(X810_SHA256, asset_stager)
        self.assertIn('vpu_src="${GTS9_VPU_MBN:-}"', stage)
        self.assertIn('"${GTS9_VPU_MBN:-}"', fetch)
        self.assertIn("not the verified SM-X810 CYG1 firmware", stage)
        self.assertIn("GTS9_VPU_MBN=/path/to/vpu30_4v.mbn", fetch)
        self.assertIn("local-assets/firmware-overrides", asset_stager)

    def test_public_sibling_blob_is_not_fetched_for_x810(self) -> None:
        for path in (STAGER, ROOT / "rootfs/fetch-local-assets.sh"):
            source = path.read_text()
            self.assertNotIn("raw.githubusercontent.com/Azkali/gts9wifi-firmware/main/qcom/sm8550/gts9wifi", source)
            self.assertNotIn("curl -fsSL --max-time 120 -o \"$vpu", source)
        self.assertIn(SIBLING_SHA256, STAGER.read_text())


if __name__ == "__main__":
    unittest.main()
