#!/usr/bin/env python3
"""Ensure mem-reclaim preserves the disabled CDSP's firmware reservations."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import re
import runpy
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-mem-reclaim"
FIRMWARE_LAYOUT = ROOT / "tools/verify-x810-cdsp-firmware-layout.py"
DTS = ROOT / "kernel/files/sm8550-samsung-gts9wifi.dts"
FIRMWARE_STAGE = ROOT / "tools/stage-x810-cdsp-firmware.py"
FIRMWARE_LOCK = ROOT / "specs/x810-npu/x810-cyg1-cdsp.sha256"
PAS_NO_AUTO_BOOT = ROOT / "kernel/patches/qcom-pas-opt-out-auto-boot.patch"


def main() -> None:
    # This production helper is copied verbatim into the rootfs overlay. Keep
    # tests from depositing an ignored __pycache__ there before the image build.
    helper = SimpleNamespace(**runpy.run_path(str(HELPER)))

    required = {
        "cdsp-region@9c900000",
        "q6-cdsp-dtb-region@9e900000",
        "cdsp-secure-heap-region@82800000",
    }
    removed = required.intersection(helper.REGIONS)
    assert not removed, f"mem-reclaim must preserve CDSP memory: {sorted(removed)}"
    assert ("/soc@0/remoteproc@32300000", "memory-region") not in helper.DANGLING
    assert "mpss-region@8a800000" in helper.REGIONS

    # Keep the exact X810 CYG1 PAS firmware lookup reproducible, but do not
    # enable the CDSP. The SM8550 PAS driver requests firmware-name[0] for the
    # CDSP and firmware-name[1] for its separately authenticated DT image.
    dts = DTS.read_text(encoding="utf-8")
    blocks = re.findall(r"&remoteproc_cdsp\s*\{([^}]+)\};", dts, re.S)
    assert len(blocks) == 1, f"expected one X810 CDSP override, found {len(blocks)}"
    block = blocks[0]
    prop = re.search(r"firmware-name\s*=\s*([^;]+);", block, re.S)
    assert prop, "X810 CDSP firmware-name property is missing"
    names = re.findall(r'"([^"\\]+)"', prop.group(1))
    assert names == [
        "qcom/sm8550/cdsp.mdt",
        "qcom/sm8550/cdsp_dtb.mdt",
    ], f"unexpected X810 PAS firmware order/paths: {names}"
    assert re.search(r'\bstatus\s*=\s*"disabled"\s*;', block), (
        "CDSP must remain disabled until X810 PAS/FastRPC/HTP behavior is validated"
    )
    assert re.search(r"\bqcom,no-auto-boot\s*;", block), (
        "an opt-in enabled candidate must not auto-start CDSP during probe"
    )

    # Upstream SM8550's PAS descriptor has auto_boot=true. This opt-out patch
    # is deliberately property-gated, so it has no effect on other boards.
    # kernel/prepare.sh applies all board-port patches before building.
    pas_patch = PAS_NO_AUTO_BOOT.read_text(encoding="utf-8")
    assert '"qcom,no-auto-boot"' in pas_patch
    assert "rproc->auto_boot = false;" in pas_patch
    prepare = (ROOT / "kernel/prepare.sh").read_text(encoding="utf-8")
    assert '"$here"/patches/*.patch' in prepare

    # Match the DT paths to the ignored-only stage destination and CYG1 file
    # lock so they cannot drift to another board's firmware. This deliberately
    # does not imply those staged files enter the default rootfs.
    stage = FIRMWARE_STAGE.read_text(encoding="utf-8")
    assert 'staged / "usr/lib/firmware/qcom/sm8550"' in stage
    assert 'staged / "usr/share/qcom/sm8550/Samsung/gts9pwifi/cdsp"' in stage
    assert "if LOCAL_ASSETS.is_symlink():" in stage
    locked_paths = {
        line.split()[1]
        for line in FIRMWARE_LOCK.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert len(locked_paths) == 43, f"expected the exact 43-file CYG1 lock, got {len(locked_paths)}"
    assert {"cdsp/cdsp.mdt", "cdsp/cdsp_dtb.mdt"} <= locked_paths
    assert "dsp/cdsp/fastrpc_shell_unsigned_3" in locked_paths

    config = (ROOT / "kernel/files/config-gts9wifi.fragment").read_text(encoding="utf-8")
    for symbol in ("CONFIG_REMOTEPROC=y", "CONFIG_QCOM_Q6V5_PAS=y", "CONFIG_QCOM_FASTRPC=y"):
        assert symbol in config, f"required kernel support missing: {symbol}"

    # Firmware staging and any NPU activation remain outside fresh-rootfs and
    # public release paths; a future firmware-only local package is inert.
    rootfs_builder = (ROOT / "rootfs/build-rootfs.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/x810-fedora.yml").read_text(encoding="utf-8")
    assert "stage-x810-cdsp-firmware.py" not in rootfs_builder
    assert "npu-firmware-x810-cyg1" not in workflow

    # Test the offline MDT parser against a valid ELF fixture and ensure it
    # rejects an out-of-carveout load and an incomplete payload set. This does
    # not boot or stage CDSP firmware.
    layout_loader = SourceFileLoader("x810_cdsp_firmware_layout", str(FIRMWARE_LAYOUT))
    layout_spec = spec_from_loader(layout_loader.name, layout_loader)
    if layout_spec is None or layout_spec.loader is None:
        raise SystemExit("cannot import offline CDSP firmware layout validator")
    layout = module_from_spec(layout_spec)
    sys.modules[layout_spec.name] = layout
    layout_spec.loader.exec_module(layout)
    layout.self_test()
    print("X810 CDSP reservations retained; verified-unused MPSS reclamation remains")


if __name__ == "__main__":
    main()
