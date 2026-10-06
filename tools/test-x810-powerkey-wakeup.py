#!/usr/bin/env python3
"""Test the PMIC power-key wake-event filter and its single-owner wiring."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/overlay/usr/libexec/x810-powerkey"
SERVICE = ROOT / "rootfs/overlay/usr/lib/systemd/system/x810-powerkey.service"
LOGIND = ROOT / "rootfs/overlay/etc/systemd/logind.conf.d/20-x810-powerkey.conf"
DCONF = ROOT / "rootfs/overlay/etc/dconf/db/local.d/90-x810-powerkey"
PRESET = ROOT / "rootfs/overlay/usr/lib/systemd/system-preset/85-gts9wifi.preset"
SPEC = ROOT / "specs/x810-fedora-port.spec"


def load_helper():
    # The installed helper intentionally has no .py suffix because it is an
    # executable under /usr/libexec. Tell importlib explicitly that it is
    # Python source instead of relying on extension-based loader detection.
    loader = SourceFileLoader("x810_powerkey", str(HELPER))
    spec = spec_from_loader("x810_powerkey", loader)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_swallow_matching_power_key_wakes():
    helper = load_helper()
    assert helper.MIN_SLEEP_S == 0.05
    assert helper.suspend_time_delta(12.0, 13.5) == 1.5
    assert helper.suspend_time_delta(13.5, 13.0) == 0.0
    assert helper.is_powerkey_wake(0.05, 21, 21)
    assert helper.is_powerkey_wake(3.0, 21, 21)
    assert not helper.is_powerkey_wake(0.049, 21, 21)
    assert not helper.is_powerkey_wake(3.0, 192, 21)
    assert not helper.is_powerkey_wake(3.0, 21, None)


def test_service_is_the_only_power_button_action_owner():
    service = SERVICE.read_text()
    logind = LOGIND.read_text()
    dconf = DCONF.read_text()
    preset = PRESET.read_text()
    spec = SPEC.read_text()

    assert "ExecStart=/usr/libexec/x810-powerkey" in service
    assert "Restart=always" in service
    assert "HandlePowerKey=ignore" in logind
    assert "HandlePowerKeyLongPress=ignore" in logind
    assert "power-button-action='nothing'" in dconf
    assert "enable x810-powerkey.service" in preset
    assert "systemctl enable x810-powerkey.service" in spec
    assert "systemctl start x810-powerkey.service" in spec
    assert "dconf update" in spec


if __name__ == "__main__":
    test_only_swallow_matching_power_key_wakes()
    test_service_is_the_only_power_button_action_owner()
    print("X810 power-key wake filtering tests passed")
