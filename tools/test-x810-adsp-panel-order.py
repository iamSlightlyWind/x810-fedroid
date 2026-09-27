#!/usr/bin/env python3
"""Keep X810 ADSP startup behind the panel cold-boot platform-resume cycle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADSP_ORDER = ROOT / "rootfs/overlay/etc/systemd/system/gts9wifi-adsp-boot.service.d/10-ordering.conf"
PANEL_UNIT = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-panel-coldboot-recover.service"


def main() -> None:
    order = ADSP_ORDER.read_text(encoding="utf-8")
    panel = PANEL_UNIT.read_text(encoding="utf-8")
    assert "Wants=gts9wifi-panel-coldboot-recover.service" in order
    assert "After=gts9wifi-panel-coldboot-recover.service" in order
    assert "pm_test" in panel
    print("PASS: the X810 ADSP wants and starts after panel cold-boot recovery")


if __name__ == "__main__":
    main()
