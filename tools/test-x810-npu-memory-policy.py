#!/usr/bin/env python3
"""Ensure mem-reclaim preserves the disabled CDSP's firmware reservations."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-mem-reclaim"


def main() -> None:
    loader = SourceFileLoader("gts9wifi_mem_reclaim", str(HELPER))
    spec = spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot import mem-reclaim policy")
    helper = module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)

    required = {
        "cdsp-region@9c900000",
        "q6-cdsp-dtb-region@9e900000",
        "cdsp-secure-heap-region@82800000",
    }
    removed = required.intersection(helper.REGIONS)
    assert not removed, f"mem-reclaim must preserve CDSP memory: {sorted(removed)}"
    assert ("/soc@0/remoteproc@32300000", "memory-region") not in helper.DANGLING
    assert "mpss-region@8a800000" in helper.REGIONS
    print("X810 CDSP reservations retained; verified-unused MPSS reclamation remains")


if __name__ == "__main__":
    main()
