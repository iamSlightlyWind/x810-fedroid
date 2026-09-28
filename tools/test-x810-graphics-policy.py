#!/usr/bin/env python3
"""Guard the system-wide accelerated GTK rendering policy for the X810."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "rootfs/overlay/etc/environment.d/91-x810-gtk-rendering.conf"
RPM_BUILDER = ROOT / "tools/build-port-support-rpm.sh"
RPM_CONTRACT = ROOT / "tools/test-port-build-contract.py"
ROOTFS_BUILDER = ROOT / "rootfs/build-rootfs.sh"


def main() -> None:
    config = POLICY.read_text(encoding="utf-8")
    settings = [line.strip() for line in config.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    if settings != ["GSK_GPU_DISABLE=merge"]:
        raise SystemExit(f"unexpected X810 GTK graphics policy: {settings!r}")

    # This is a targeted GTK GSK batching workaround, not CPU rendering or a
    # global backend override. Keep Vulkan/GL selection under GTK/Mesa control.
    forbidden = ("GSK_RENDERER=", "LIBGL_ALWAYS_SOFTWARE=1",
                 "MESA_LOADER_DRIVER_OVERRIDE=llvmpipe")
    if any(value in config for value in forbidden):
        raise SystemExit("X810 graphics policy must not select a software renderer")

    package = RPM_BUILDER.read_text(encoding="utf-8")
    path = "/etc/environment.d/91-x810-gtk-rendering.conf"
    if path not in package:
        raise SystemExit("support RPM build does not require the GTK graphics policy")

    contract = RPM_CONTRACT.read_text(encoding="utf-8")
    if path not in contract:
        raise SystemExit("support RPM contract does not verify GTK policy ownership/content")

    rootfs_builder = ROOTFS_BUILDER.read_text(encoding="utf-8")
    if not re.search(r"cp\s+-a\s+.*overlay", rootfs_builder):
        raise SystemExit("rootfs builder no longer stages the source overlay")

    print("X810 graphics policy: global GTK merge workaround, no renderer/software fallback")


if __name__ == "__main__":
    main()
