#!/usr/bin/env python3
"""Check X810 PD limits and static charger-owner handoff invariants."""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "kernel/files/x810_pd_limits.h"
BATTERY_DRIVER = ROOT / "kernel/files/sm5714_battery.c"
DIRECT_DRIVER = ROOT / "kernel/files/sm5440_direct.c"


def function_body(source: str, signature: str) -> str:
    """Return a C function body using simple brace balancing."""
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for offset in range(opening, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:offset]
    raise AssertionError(f"unterminated function: {signature}")


def check_charger_arbitration() -> None:
    battery = BATTERY_DRIVER.read_text()
    direct = DIRECT_DRIVER.read_text()

    configure = function_body(battery, "static int sm5714_configure_charging(")
    lock = configure.index("mutex_lock(&sm->chg_lock)")
    owner_guard = configure.index("if (READ_ONCE(sm->direct_charging))")
    first_charger_access = min(
        pos for pos in (
            configure.find("sm5714_get_usb_type(sm)"),
            configure.find("i2c_smbus_write_byte_data"),
            configure.find("sm5714_chg_update_bits"),
        ) if pos >= 0
    )
    assert lock < owner_guard < first_charger_access, (
        "switching-charger configuration must hold chg_lock and defer before "
        "charger I/O while SM5440 owns the pack"
    )
    assert "ret = 0;\n\t\tgoto out_unlock;" in configure[owner_guard:], (
        "owner deferral should report success so policy updates can be queued"
    )

    store = function_body(battery, "static ssize_t fast_charge_store(")
    global_lock = store.index("mutex_lock(&sm5714_global_lock)")
    unlock = store.index("mutex_unlock(&sm5714_global_lock)")
    assert global_lock < store.index("old_enabled = READ_ONCE(sm->fast_charge);")
    assert global_lock < store.index("WRITE_ONCE(sm->fast_charge, enabled);")
    assert global_lock < store.index("ret = sm5714_configure_charging(sm);") < unlock
    assert "return " not in store[global_lock:unlock], (
        "all fast-charge store exits after taking the global lock must unlock first"
    )
    assert store.rstrip().endswith("return ret;"), (
        "fast-charge store must return only after releasing the global lock"
    )
    assert "old_enabled = READ_ONCE(sm->fast_charge);" in store
    assert "WRITE_ONCE(sm->fast_charge, enabled);" in store
    assert "WRITE_ONCE(sm->fast_charge, old_enabled);" in store
    assert "restore_ret = sm5714_configure_charging(sm);" in store, (
        "failed sysfs updates must try to restore the previous charger policy"
    )

    pump_off = function_body(direct, "static int sm5440_pump_off(")
    assert "SM5440_CNTL5_OP_MODE_MASK, 0" in pump_off
    assert "i2c_smbus_read_byte_data(sm->client, SM5440_REG_CNTL5)" in pump_off
    assert "if (mode & SM5440_CNTL5_OP_MODE_MASK)\n\t\treturn -EIO;" in pump_off, (
        "pump shutdown must be verified by reading back the operating mode"
    )

    restore = function_body(direct, "static int sm5440_restore_switching(")
    park = restore.index("ret = sm5440_pump_off(sm);")
    assert "if (ret)\n\t\tgoto failed;" in restore[park:]
    fixed_pd = restore.index("POWER_SUPPLY_PROP_ONLINE, 1")
    release_owner = restore.index("sm5714_battery_set_direct_charge(false)")
    assert park < fixed_pd < release_owner, (
        "handoff must park SM5440, request fixed PD, then release SM5714 ownership"
    )
    assert "if (ret)\n\t\tgoto failed;" in restore[fixed_pd:release_owner], (
        "a failed fixed-PD request must retain charger ownership"
    )
    assert restore.index("sm->restore_pending = false;") > release_owner

    notifier = function_body(direct, "static int sm5440_pm_notify(")
    for event in ("PM_SUSPEND_PREPARE", "PM_HIBERNATION_PREPARE",
                  "PM_RESTORE_PREPARE", "PM_POST_SUSPEND",
                  "PM_POST_HIBERNATION", "PM_POST_RESTORE"):
        assert f"case {event}:" in notifier, (
            f"charger handoff notifier must cover {event}"
        )
    assert "return NOTIFY_BAD;" in notifier, (
        "suspend/hibernation must be vetoed if safe charger handoff fails"
    )

    assert ".suppress_bind_attrs = true" in direct, (
        "manual driver bind/unbind must be disabled for the direct charger"
    )
    config = (ROOT / "kernel/files/config-gts9wifi.fragment").read_text()
    assert "CONFIG_CHARGER_SM5440_DIRECT=y" in config, (
        "the production X810 config must build the charger in-kernel, not as an unloadable module"
    )
    cleanup = function_body(direct, "static void sm5440_cancel_work(")
    assert "sm5440_restore_switching(sm)" in cleanup
    assert "driver cleanup could not verify safe charger handoff" in cleanup, (
        "cleanup must report when best-effort handoff cannot be proven"
    )

    release = function_body(
        battery, "int sm5714_battery_set_direct_charge(bool active)\n{")
    assert "\t\tif (ret)\n\t\t\tWRITE_ONCE(sm->direct_charging, true);" in release, (
        "a failed switching-path reconfiguration must retain direct ownership"
    )


def main() -> None:
    source = r'''#include <stdio.h>
#include "x810_pd_limits.h"
struct test_case { int direct; unsigned int mv, ma; int valid; };
static const struct test_case cases[] = {
    { 0, 0, 0, 1 },
    { 0, 0, 3000, 1 },
    { 0, 0, 3001, 0 },
    { 0, 4999, 500, 0 },
    { 0, 5000, 3000, 1 },
    { 0, 9000, 3000, 1 },
    { 0, 9001, 1000, 0 },
    { 0, 9000, 3001, 0 },
    { 1, 0, 3000, 1 },
    { 1, 0, 3001, 0 },
    { 1, 5000, 5000, 1 },
    { 1, 10500, 5000, 1 },
    { 1, 10501, 3000, 0 },
    { 1, 10500, 5001, 0 },
};
int main(void) {
    unsigned int i;
    static const struct { unsigned int requested, expected; } pps_limits[] = {
        { 0, 1800 }, { 1799, 1800 }, { 1800, 1800 }, { 1849, 1800 },
        { 1850, 1850 }, { 2425, 2400 }, { 4999, 4950 }, { 5000, 5000 },
        { 5001, 5000 },
    };
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        int got = x810_pd_contract_is_valid(cases[i].direct,
                                            cases[i].mv, cases[i].ma);
        if (got != cases[i].valid) {
            fprintf(stderr, "case %u: direct=%d mv=%u ma=%u: got %d, want %d\n",
                    i, cases[i].direct, cases[i].mv, cases[i].ma,
                    got, cases[i].valid);
            return 1;
        }
    }
    for (i = 0; i < sizeof(pps_limits) / sizeof(pps_limits[0]); i++) {
        unsigned int got = x810_pd_pps_current_limit(pps_limits[i].requested);
        if (got != pps_limits[i].expected || got % 50) {
            fprintf(stderr,
                    "PPS limit %u mA: got %u mA, want %u mA in 50 mA steps\n",
                    pps_limits[i].requested, got, pps_limits[i].expected);
            return 1;
        }
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="x810-pd-test-") as temp:
        temp_path = Path(temp)
        c_file = temp_path / "test.c"
        binary = temp_path / "test"
        c_file.write_text(source)
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-I", str(HEADER.parent), str(c_file), "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True)
    check_charger_arbitration()
    print("X810 PD/PPS limits and charger-owner handoff invariants passed")


if __name__ == "__main__":
    main()
