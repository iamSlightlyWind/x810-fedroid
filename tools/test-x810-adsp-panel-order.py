#!/usr/bin/env python3
"""Keep X810 ADSP startup behind the panel cold-boot platform-resume cycle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADSP_ORDER = ROOT / "rootfs/overlay/etc/systemd/system/gts9wifi-adsp-boot.service.d/10-ordering.conf"
PANEL_UNIT = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-panel-coldboot-recover.service"
ADSP_UNIT = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-adsp-boot.service"
WAIT_FASTRPC = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-wait-fastrpc"
ADSP_START = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-adsp-boot"


def main() -> None:
    order = ADSP_ORDER.read_text(encoding="utf-8")
    panel = PANEL_UNIT.read_text(encoding="utf-8")
    adsp = ADSP_UNIT.read_text(encoding="utf-8")
    helper = WAIT_FASTRPC.read_text(encoding="utf-8")
    start = ADSP_START.read_text(encoding="utf-8")
    assert "Requires=gts9wifi-panel-coldboot-recover.service" in order
    assert "After=gts9wifi-panel-coldboot-recover.service" in order
    assert "pm_test" in panel
    assert "ExecStartPre=/bin/sleep 25" in adsp
    assert "ExecStart=/usr/libexec/gts9wifi-adsp-boot" in adsp
    assert "Requires=vendor-firmware_mnt.mount" in order
    assert "After=vendor-firmware_mnt.mount" in order
    assert "source_dir=/vendor/firmware_mnt/image" in start
    assert "refusing non-read-only APNHLOS mount" in start
    assert '"$source_dir/adsp_dtb.mdt"' in start
    assert "incomplete CYG1 ADSP segment set" in start
    assert "install -m 0644" in start
    assert "TimeoutStartSec=110" in adsp
    assert "-lt 60" in helper and "udevadm settle --timeout=10" in helper
    assert 110 >= 25 + 60 + 10
    print("PASS: the X810 ADSP requires panel recovery and its start deadline covers FastRPC readiness")


if __name__ == "__main__":
    main()
