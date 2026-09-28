#!/usr/bin/env python3
"""Ensure module-BTF mismatches cannot prevent ordinary kernel modules loading."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
base = (ROOT / "kernel/files/config-mainline.aarch64").read_text(encoding="utf-8")
fragment = (ROOT / "kernel/files/config-gts9wifi.fragment").read_text(encoding="utf-8")
spec = (ROOT / "kernel/kernel.spec").read_text(encoding="utf-8")

assert "CONFIG_DEBUG_INFO_BTF_MODULES=y" in base, "module BTF is not enabled in the base config"
assert "CONFIG_MODULE_ALLOW_BTF_MISMATCH=y" in fragment, (
    "X810 kernel must allow modules to load without mismatched optional BTF"
)
assert "CONFIG_MODULE_ALLOW_BTF_MISMATCH=y; do" in spec, (
    "kernel RPM build must fail if the mismatch-tolerant config was lost"
)

print("PASS: mismatched optional module BTF is non-fatal, with an RPM build contract")
