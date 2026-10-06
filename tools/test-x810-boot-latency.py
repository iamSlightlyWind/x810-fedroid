#!/usr/bin/env python3
"""Guard against optional hardware probes delaying the X810 desktop boot."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    device_control = (ROOT / "rootfs/overlay/usr/libexec/gts9wifi-device-control").read_text()
    chrony = (ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-chronyd.service").read_text()
    audio_unit = (ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-audio-session.service").read_text()
    audio_timer = (ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-audio-session.timer").read_text()
    sensor_unit = (ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-wait-sensor-proxy.service").read_text()
    sensor_timer = (ROOT / "rootfs/overlay/usr/lib/systemd/system/gts9wifi-wait-sensor-proxy.timer").read_text()
    user_stop_timeout = (ROOT / "rootfs/overlay/etc/systemd/system/user@.service.d/90-x810-stop-timeout.conf").read_text()
    spec = (ROOT / "specs/x810-fedora-port.spec").read_text()

    # Missing optional sysfs controls must be skipped; waiting for absent I2C
    # devices used to add a full 20 seconds before GNOME could start.
    assert 'if [ ! -e "$node" ] && [ -d "${node%/*}" ]; then' in device_control
    assert '[ "$tries" -lt 2 ]' in device_control
    assert 'is not exposed by this kernel; skipped' in device_control
    assert '[ "$tries" -lt 20 ]' not in device_control

    # NTP is needed eventually on this RTC-less tablet, but a DNS retry loop
    # must not hold the boot target or Plymouth open.
    assert "ExecStartPost=/bin/sh -c 'chronyc -a reload sources" in chrony
    assert "sleep 10" not in chrony
    assert "for i in $(seq 1 30)" not in chrony

    # The audio recovery waits for the ADSP and can take ~30s. Start it after
    # GDM, not as a multi-user prerequisite for the login screen.
    assert "After=display-manager.service" in audio_unit
    assert "Before=display-manager.service" not in audio_unit
    assert "Wants=gts9wifi-adsp-boot.service" in audio_unit
    assert "pd-mapper.service" in audio_unit
    assert "hexagonrpcd-adsp-rootpd.service" in audio_unit
    assert "OnBootSec=5s" in audio_timer
    assert "WantedBy=timers.target" in audio_timer
    assert "systemctl disable gts9wifi-audio-session.service" in spec
    assert "systemctl enable gts9wifi-audio-session.timer" in spec
    assert "systemctl start gts9wifi-audio-session.timer" in spec
    assert "After=display-manager.service" in sensor_unit
    assert "WantedBy=graphical.target" not in sensor_unit
    assert "OnBootSec=15s" in sensor_timer
    assert "WantedBy=timers.target" in sensor_timer
    assert "systemctl disable gts9wifi-wait-sensor-proxy.service" in spec
    assert "systemctl enable gts9wifi-wait-sensor-proxy.timer" in spec
    # Linger is enabled for the Tab Companion Firefox memory agent. The user
    # manager gets a 30s graceful-stop budget; only its failure path is forced.
    assert "TimeoutStopSec=30s" in user_stop_timeout
    assert "TimeoutStopFailureMode=kill" in user_stop_timeout
    preset = (ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/85-gts9wifi.preset").read_text()
    assert "disable pd-mapper.service" in preset
    assert "disable hexagonrpcd-adsp-rootpd.service" in preset
    assert "enable gts9wifi-audio-session.timer" in preset
    print("PASS: optional controls and chrony retry no longer impose long boot waits")


if __name__ == "__main__":
    main()
