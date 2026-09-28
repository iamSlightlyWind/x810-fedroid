#!/usr/bin/env python3
"""Contract-check pd-mapper ordering against the late X810 ADSP startup."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DROPIN = ROOT / "rootfs/overlay/etc/systemd/system/pd-mapper.service.d/10-gts9wifi-adsp-order.conf"
ADSP_ORDER = ROOT / "rootfs/overlay/etc/systemd/system/gts9wifi-adsp-boot.service.d/10-ordering.conf"
ADSP_UNIT = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-adsp-boot.service"
BUILDER = ROOT / "rootfs/build-rootfs.sh"


def main() -> None:
    dropin = DROPIN.read_text(encoding="utf-8")
    adsp_order = ADSP_ORDER.read_text(encoding="utf-8")
    adsp_unit = ADSP_UNIT.read_text(encoding="utf-8")
    builder = BUILDER.read_text(encoding="utf-8")

    assert "[Unit]" in dropin
    assert "After=gts9wifi-adsp-boot.service" in dropin
    assert "Requires=gts9wifi-adsp-boot.service" in dropin
    assert "[Service]" in dropin
    assert "Restart=on-failure" in dropin
    assert "RestartSec=3" in dropin

    # The ordered dependency must retain this port's own late, panel-safe
    # ADSP start rather than introducing an independent/early remoteproc path.
    assert "Requires=gts9wifi-panel-coldboot-recover.service" in adsp_order
    assert "After=gts9wifi-panel-coldboot-recover.service" in adsp_order
    assert "ExecStartPre=/bin/sleep 25" in adsp_unit
    assert "TimeoutStartSec=110" in adsp_unit
    assert "pd-mapper" in builder.split("for unit in \\\n", 1)[1].split("\ndo\n", 1)[0]

    print("PASS: pd-mapper waits for the panel-ordered, late X810 ADSP and retries failures")


if __name__ == "__main__":
    main()
