#!/usr/bin/env python3
"""Keep X810 ADSP startup behind the panel cold-boot platform-resume cycle."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADSP_ORDER = ROOT / "rootfs/overlay/etc/systemd/system/gts9wifi-adsp-boot.service.d/10-ordering.conf"
PANEL_UNIT = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-panel-coldboot-recover.service"
ADSP_UNIT = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-adsp-boot.service"
WAIT_FASTRPC = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-wait-fastrpc"
ADSP_START = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-adsp-boot"
PD_MAPPER_ORDER = ROOT / "rootfs/overlay/etc/systemd/system/pd-mapper.service.d/10-gts9wifi-adsp-order.conf"
ROOTPD_ORDER = ROOT / "rootfs/overlay/etc/systemd/system/hexagonrpcd-adsp-rootpd.service.d/10-ordering.conf"
HEXAGONRPCD_UNIT_PATCH = ROOT / "specs/hexagonrpcd-samsung/patches/systemd-services.patch"
SYSTEMD_PRESET = ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/85-gts9wifi.preset"
AUDIO_SESSION = ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-audio-session.service"
ROOTFS_BUILD = ROOT / "rootfs/build-rootfs.sh"


def main() -> None:
    order = ADSP_ORDER.read_text(encoding="utf-8")
    panel = PANEL_UNIT.read_text(encoding="utf-8")
    adsp = ADSP_UNIT.read_text(encoding="utf-8")
    helper = WAIT_FASTRPC.read_text(encoding="utf-8")
    start = ADSP_START.read_text(encoding="utf-8")
    mapper_order = PD_MAPPER_ORDER.read_text(encoding="utf-8")
    rootpd_order = ROOTPD_ORDER.read_text(encoding="utf-8")
    hexagonrpcd_units = HEXAGONRPCD_UNIT_PATCH.read_text(encoding="utf-8")
    preset = SYSTEMD_PRESET.read_text(encoding="utf-8")
    audio_session = AUDIO_SESSION.read_text(encoding="utf-8")
    rootfs_build = ROOTFS_BUILD.read_text(encoding="utf-8")
    assert "Requires=gts9wifi-panel-coldboot-recover.service" in order
    assert "After=gts9wifi-panel-coldboot-recover.service" in order
    assert "pm_test" in panel
    assert "ExecStartPre=/bin/sleep 25" in adsp
    assert "ExecStart=/usr/libexec/gts9wifi-adsp-boot" in adsp
    assert "Requires=vendor-firmware_mnt.mount" in order
    assert "After=vendor-firmware_mnt.mount" in order
    assert "Requires=gts9wifi-adsp-boot.service" in mapper_order
    assert "After=gts9wifi-adsp-boot.service" in mapper_order
    assert "Wants=gts9wifi-adsp-boot.service" not in mapper_order
    # Keep ADSP clients off the boot-critical enablement path. The delayed
    # audio-session timer pulls them in on demand; their ordering drop-ins then
    # bring up the panel-ordered firmware before evaluating FastRPC conditions.
    assert "Requires=gts9wifi-adsp-boot.service" in rootpd_order
    assert "After=gts9wifi-adsp-boot.service" in rootpd_order
    assert "ConditionPathExists=/dev/fastrpc-adsp" in hexagonrpcd_units
    assert "Wants=gts9wifi-adsp-boot.service hexagonrpcd-adsp-rootpd.service pd-mapper.service" in audio_session
    assert "After=systemd-logind.service systemd-user-sessions.service gts9wifi-adsp-boot.service hexagonrpcd-adsp-rootpd.service pd-mapper.service" in audio_session
    assert "disable hexagonrpcd-adsp-rootpd.service" in preset
    assert "disable pd-mapper.service" in preset
    enable_block = rootfs_build.split("for unit in \\\n", 1)[1].split("\ndo", 1)[0]
    assert "hexagonrpcd-adsp-rootpd" not in enable_block
    assert "pd-mapper" not in enable_block
    assert "source_dir=/vendor/firmware_mnt/image" in start
    assert "refusing non-read-only APNHLOS mount" in start
    assert '"$source_dir/adsp_dtb.mdt"' in start
    for mapping in ("adspr.jsn", "adsps.jsn", "adspua.jsn", "cdspr.jsn"):
        assert mapping in start, f"ADSP helper must stage the device-local {mapping} map"
    assert 'cp -- "$source_dir/$name" "$stage/$name"' in start
    assert "never substitute the sibling X910/X710 maps" in start
    assert "APNHLOS exposed $have_pd_maps/4 protection-domain maps" in start
    assert "incomplete CYG1 ADSP segment set" in start
    assert "install -m 0644" in start
    assert "TimeoutStartSec=110" in adsp
    assert "-lt 60" in helper and "udevadm settle --timeout=10" in helper
    assert 110 >= 25 + 60 + 10
    print("PASS: the X810 ADSP requires panel recovery and its start deadline covers FastRPC readiness")


if __name__ == "__main__":
    main()
