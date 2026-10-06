#!/usr/bin/env python3
"""Exercise FTS touchscreen wake policy and migration in an isolated fake sysfs."""

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-device-control"
RULE = ROOT / "rootfs/overlay/usr/lib/udev/rules.d/71-gts9wifi-touchscreen-perms.rules"


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def run(helper_env, *args):
    return subprocess.run([str(HELPER), *args], env=helper_env,
                          text=True, capture_output=True, check=True)


def main():
    with tempfile.TemporaryDirectory(prefix="x810-device-control-") as tmp:
        base = Path(tmp)
        sysroot = base / "sys"
        state = base / "state"
        fts = sysroot / "bus/i2c/devices/11-0049"
        write(fts / "name", "fts1ba90a\n")
        write(fts / "double_tap_to_wake", "1\n")
        write(fts / "power/wakeup", "enabled\n")
        state.mkdir()
        # Prior releases accidentally persisted the old enabled default.
        write(state / "double-tap-to-wake", "1\n")
        env = os.environ.copy()
        env["GTS9WIFI_SYSFS_ROOT"] = str(sysroot)
        env["GTS9WIFI_STATE_DIR"] = str(state)

        run(env, "apply")
        assert (fts / "double_tap_to_wake").read_text() == "0\n"
        assert (fts / "power/wakeup").read_text() == "disabled\n"
        assert (state / "double-tap-to-wake").read_text() == "0\n"
        assert (state / "device-control-schema").read_text() == "2\n"

        run(env, "set", "double-tap-to-wake", "1")
        assert (fts / "double_tap_to_wake").read_text() == "1\n"
        assert (fts / "power/wakeup").read_text() == "enabled\n"
        run(env, "set", "double-tap-to-wake", "0")
        assert (fts / "double_tap_to_wake").read_text() == "0\n"
        assert (fts / "power/wakeup").read_text() == "disabled\n"

    rule = RULE.read_text()
    assert 'ATTR{name}=="fts1ba90a"' in rule
    assert "power/wakeup" in rule
    print("X810 device-control wake-policy tests passed")


if __name__ == "__main__":
    main()
