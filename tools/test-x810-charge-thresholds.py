#!/usr/bin/env python3
"""Offline tests for the X810 standard charge start/end threshold policy."""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "kernel/files/x810_charge_thresholds.h"
BATTERY = ROOT / "kernel/files/sm5714_battery.c"
DIRECT = ROOT / "kernel/files/sm5440_direct.c"


def check_policy() -> None:
    source = r'''#include <assert.h>
#include "x810_charge_thresholds.h"

int main(void)
{
    bool stopped = false;

    /* Disabled-by-default policy preserves ordinary 0..100% charging. */
    assert(x810_charge_thresholds_valid(0, 100));
    assert(x810_charge_threshold_default_start(100) == 0);
    assert(x810_charge_threshold_default_start(80) == 70);
    assert(x810_charge_threshold_default_start(10) == 0);
    assert(!x810_charge_thresholds_update(false, 0, 100, 100));

    /* Stop at end; retain stop through hysteresis; restart at start. */
    assert(x810_charge_thresholds_valid(70, 80));
    stopped = x810_charge_thresholds_update(stopped, 70, 80, 79);
    assert(!stopped);
    stopped = x810_charge_thresholds_update(stopped, 70, 80, 80);
    assert(stopped);
    stopped = x810_charge_thresholds_update(stopped, 70, 80, 71);
    assert(stopped);
    stopped = x810_charge_thresholds_update(stopped, 70, 80, 70);
    assert(!stopped);

    /* Setting a lower cap immediately stops if current SOC exceeds it. */
    assert(x810_charge_thresholds_update(false, 70, 80, 91));

    /* Gauge read errors cannot silently re-enable charging. */
    assert(x810_charge_thresholds_update(true, 70, 80, -1));
    assert(!x810_charge_thresholds_update(false, 70, 80, -1));

    /* Invalid ordering/ranges are rejected; end=100 disables the cap. */
    assert(!x810_charge_thresholds_valid(80, 80));
    assert(!x810_charge_thresholds_valid(81, 80));
    assert(!x810_charge_thresholds_valid(0, 0));
    assert(!x810_charge_thresholds_valid(-1, 80));
    assert(!x810_charge_thresholds_valid(0, 101));
    assert(!x810_charge_thresholds_update(true, 70, 100, 90));
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="x810-charge-threshold-") as temp:
        c_file = Path(temp) / "test.c"
        binary = Path(temp) / "test"
        c_file.write_text(source)
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-I", str(HEADER.parent), str(c_file), "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True)


def check_driver_wiring() -> None:
    battery = BATTERY.read_text()
    direct = DIRECT.read_text()

    assert "sm->charge_control_start_threshold = 0;" in battery
    assert "sm->charge_control_end_threshold = 100;" in battery
    assert "POWER_SUPPLY_PROP_CHARGE_CONTROL_START_THRESHOLD" in battery
    assert "POWER_SUPPLY_PROP_CHARGE_CONTROL_END_THRESHOLD" in battery
    assert "x810_charge_threshold_default_start(val->intval)" in battery, (
        "an end-only write needs Samsung's source-backed 10-point hysteresis"
    )
    assert ".set_property\t= sm5714_bat_set_property" in battery
    assert ".property_is_writeable = sm5714_bat_property_is_writeable" in battery

    configure = battery.split("static int sm5714_configure_charging(", 1)[1]
    assert "if (READ_ONCE(sm->charge_limit_active))" in configure
    assert "SM5714_CHG_CNTL1_ENQ4FET, 0" in configure
    assert "bool sm5714_battery_charge_limit_reached(int capacity)" in battery
    assert "!READ_ONCE(sm->charge_limit_active)" in battery, (
        "the recovery poll must not re-close Q4 while a cap is active"
    )

    assert "sm5714_battery_charge_limit_reached(capacity)" in direct
    assert "charge end threshold reached" in direct
    assert "sm5714_battery_set_direct_charge(false)" in direct, (
        "direct charging must hand the battery back before threshold policy applies"
    )


def main() -> None:
    check_policy()
    check_driver_wiring()
    print("X810 charge threshold policy and SM5714/SM5440 wiring passed")


if __name__ == "__main__":
    main()
