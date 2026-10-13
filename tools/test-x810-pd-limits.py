#!/usr/bin/env python3
"""Check X810 PD limits and static charger-owner handoff invariants."""

from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "kernel/files/x810_pd_limits.h"
BATTERY_DRIVER = ROOT / "kernel/files/sm5714_battery.c"
DIRECT_DRIVER = ROOT / "kernel/files/sm5440_direct.c"
DTS = ROOT / "kernel/files/sm8550-samsung-gts9wifi.dts"
TCPM_ALERT_PATCH = ROOT / "kernel/patches/tcpm-defer-source-alert-during-pps-ams.patch"


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

    negotiate = function_body(direct, "static int sm5440_negotiate_pps(")
    target = function_body(direct, "static int sm5440_pps_target_mv(")
    eligible = function_body(
        direct, "static bool sm5440_eligible(struct sm5440_direct *sm)\n{")
    assert "#define SM5440_PPS_STEP_MA\t\tX810_PPS_CURRENT_STEP_MA" in direct
    assert "#define SM5440_MAX_PPS_MA\t\tX810_PPS_DEFAULT_MA" in direct
    assert "x810_pd_pps_current_limit(manual_pt ? SM5440_MAX_PPS_MA" in negotiate
    assert "x810_pd_pps_intersect_current(" in negotiate
    assert "POWER_SUPPLY_PROP_ONLINE, 2" in negotiate
    for prop in ("POWER_SUPPLY_PROP_VOLTAGE_MIN",
                 "POWER_SUPPLY_PROP_VOLTAGE_MAX",
                 "POWER_SUPPLY_PROP_CURRENT_MAX"):
        assert prop in negotiate, f"PPS request must use negotiated cap {prop}"
    assert "x810_pd_pps_intersect_voltage(" in negotiate
    activate_pps = negotiate.index("POWER_SUPPLY_PROP_ONLINE, 2")
    read_active_min = negotiate.index("POWER_SUPPLY_PROP_VOLTAGE_MIN")
    read_active_max = negotiate.index("POWER_SUPPLY_PROP_VOLTAGE_MAX")
    assert activate_pps < read_active_min < read_active_max, (
        "TCPM's active source-APDO bounds must be read only after PPS activation"
    )
    assert "max_uv > X810_PPS_MAX_MV * 1000" not in negotiate, (
        "a source APDO wider than the board sink range must be intersected, not rejected"
    )
    for prop in ("POWER_SUPPLY_PROP_ONLINE", "POWER_SUPPLY_PROP_USB_TYPE",
                 "POWER_SUPPLY_PROP_VOLTAGE_NOW", "POWER_SUPPLY_PROP_CURRENT_NOW"):
        assert prop in negotiate, f"PPS transition must read back {prop} after request"
    assert negotiate.index("POWER_SUPPLY_PROP_CURRENT_NOW, ma * 1000") < \
        negotiate.index("POWER_SUPPLY_PROP_VOLTAGE_NOW,\n\t\t\t       mv * 1000"), (
            "after activation, request operating current at the safe initial PPS voltage before the target voltage"
        )
    assert "max(pump_target_mv, power_floor_mv)" in target
    assert "max(mv, 8200)" in target
    assert "x810_pd_pps_fixed_entry_ready(online, contract_uv / 1000" in eligible, (
        "fixed-contract USB_TYPE must not prevent a PPS-capable source from being tried"
    )
    assert "POWER_SUPPLY_PROP_USB_TYPE" not in eligible
    assert "sm5440_psy_set" not in eligible, (
        "the preflight must remain read-only"
    )
    worker = function_body(direct, "static void sm5440_work(")
    assert "sm5440_pt_eligible(sm, false)" in worker
    assert "sm5440_start(sm, manual_pt)" in worker
    assert worker.index("sm5440_pt_eligible(sm, false)") < \
        worker.index("sm5440_start(sm, manual_pt)"), (
            "pass-through source/SOC/temp/cap preflight must run before Q4 handoff"
        )
    assert "sm->passthrough_active != READ_ONCE(manual_pass_through)" in worker
    assert "sm5440_manual_pt_adjust(sm, status2)" in worker
    pt_eligible = function_body(
        direct, "static bool sm5440_pt_eligible(struct sm5440_direct *sm, bool active)\n{")
    assert "(!active && !x810_pd_pps_fixed_entry_ready(" in pt_eligible, (
        "CV_MAN preflight uses fixed PD, but active monitoring must require ONLINE=2 PPS"
    )
    assert "active && (online != 2" in pt_eligible
    assert "type != POWER_SUPPLY_USB_TYPE_PD_PPS" in pt_eligible
    assert "online, contract_uv / 1000, contract_ua / 1000" in pt_eligible, (
        "inactive pass-through preflight must verify fixed-PD voltage/current"
    )
    assert "return true;" in pt_eligible, (
        "after common checks, both valid preflight and active PPS states must remain eligible"
    )

    # The 2026-10-04 live trace reached 9.38 V/1.8 A but then sagged to 8.803 V
    # and latched REVBLK. Check only that the candidate's *requested* normal
    # direct-charge setpoint includes its configured margin and remains inside
    # the board limit. This arithmetic is not evidence that the revised point
    # prevents another transient: the 525 mV setting has not been validated
    # live, and these assertions do not model adapter dynamics or pump startup.
    extra_headroom = int(re.search(
        r"#define\s+SM5440_EXTRA_HEADROOM_MV\s+(\d+)", direct).group(1))
    ttl_milliohm = int(re.search(
        r"#define\s+SM5440_R_TTL_MILLIOHM\s+(\d+)", direct).group(1))
    measured_vbat_mv, measured_ibus_ma = 4310, 1800
    measured_target_mv = (
        2 * measured_vbat_mv
        + (measured_ibus_ma * ttl_milliohm + 999) // 1000
        + extra_headroom
    )
    measured_target_mv = ((measured_target_mv + 19) // 20) * 20
    assert measured_target_mv == 9740, (
        f"candidate arithmetic changed unexpectedly: {measured_target_mv} mV"
    )
    assert measured_target_mv <= 10500, (
        f"candidate start point {measured_target_mv} mV exceeds the board limit"
    )
    start = function_body(direct, "static int sm5440_start(")
    assert "manual_pt ? !sm5440_pt_eligible(sm, false)" in start
    assert start.index("sm5440_pt_eligible(sm, false)") < start.index(
        "sm5714_battery_set_direct_passthrough(true)"), (
            "recheck the read-only preflight immediately before Q4 ownership changes"
        )
    assert "goto fixed;" in negotiate and \
        "sm5440_restore_fixed_pd_if_online(sm, &handoff)" in negotiate and \
        "sm5440_psy_retry(sm, POWER_SUPPLY_PROP_ONLINE, 1)" not in negotiate, (
            "failed PPS setup must use the source-aware fixed-PDO fallback"
        )

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

    # The exact active vendor_boot DTB must advertise the board's PPS sink
    # APDO, not just the concatenated DTB in boot.img. The build bundle uses
    # this source DTS to create vendor_boot. Also guard the TCPM compatibility
    # fix for the observed source Alert arriving during the sink PPS AMS.
    dts = DTS.read_text()
    assert "PDO_PPS_APDO(5000, 10500, 3000)" in dts, (
        "the board sink capabilities must advertise the 5-10.5 V/3 A PPS APDO"
    )
    tcpm_alert_patch = TCPM_ALERT_PATCH.read_text()
    assert "port->pwr_role == TYPEC_SINK" in tcpm_alert_patch
    assert "port->state == SNK_NEGOTIATE_PPS_CAPABILITIES" in tcpm_alert_patch
    assert "port->ams == POWER_NEGOTIATION" in tcpm_alert_patch
    assert "port->pps_data.active && cnt == 1" in tcpm_alert_patch
    assert "port->pending_pps_alert |=" in tcpm_alert_patch
    assert "Handling deferred source Alert after PPS AMS" in tcpm_alert_patch
    assert "port->pending_pps_alert_valid = false;" in tcpm_alert_patch

    pump_off = function_body(direct, "static int sm5440_pump_off(")
    assert "SM5440_CNTL5_OP_MODE_MASK, 0" in pump_off
    assert "i2c_smbus_read_byte_data(sm->client, SM5440_REG_CNTL5)" in pump_off
    assert "if (mode & SM5440_CNTL5_OP_MODE_MASK)\n\t\treturn -EIO;" in pump_off, (
        "pump shutdown must be verified by reading back the operating mode"
    )

    restore = function_body(direct, "static int sm5440_restore_switching(")
    park = restore.index("ret = sm5440_pump_off(sm);")
    assert "if (ret)\n\t\tgoto failed;" in restore[park:]
    fixed_pd = restore.index("sm5440_restore_fixed_pd_if_online(sm, &handoff)")
    release_owner = restore.index("sm5714_battery_set_direct_charge(false)")
    assert park < fixed_pd < release_owner, (
        "handoff must park SM5440, run source-aware PD/offline fallback, then release SM5714 ownership"
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
        battery, "static int sm5714_battery_set_direct_charge_mode(")
    vbus_check = release.index("online = sm5714_get_online(sm);")
    switching_restore = release.index(
        "ret = sm5714_configure_charging(sm);", vbus_check)
    assert vbus_check < switching_restore
    assert "else if (online > 0)" in release[vbus_check:switching_restore], (
        "an ONLINE=0 TCPM fallback may restore SM5714 only after its independent VBUS_POK check"
    )
    assert "\t\tif (ret)\n\t\t\tWRITE_ONCE(sm->direct_charging, true);" in release, (
        "a failed switching-path reconfiguration must retain direct ownership"
    )
    assert "READ_ONCE(sm->charge_limit_active) != passthrough" in release
    assert "if (passthrough && READ_ONCE(sm->fast_charge))" in release
    assert "WRITE_ONCE(sm->direct_passthrough, passthrough);" in release


def main() -> None:
    source = r'''#include <stdio.h>
#include <assert.h>
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
    { 1, 5000, 3000, 1 },
    { 1, 10500, 3000, 1 },
    { 1, 10500, 3050, 0 },
    { 1, 10501, 3000, 0 },
};
int main(void) {
    unsigned int i;
    assert(x810_pd_pps_fixed_entry_ready(1, 9000, 3000, 1800));
    assert(!x810_pd_pps_fixed_entry_ready(1, 5000, 3000, 1800));
    assert(!x810_pd_pps_fixed_entry_ready(0, 9000, 3000, 1800));
    assert(!x810_pd_pps_fixed_entry_ready(1, 9000, 1799, 1800));
    static const struct { unsigned int requested, expected; } pps_limits[] = {
        { 0, 0 }, { 1799, 0 }, { 1800, 1800 }, { 1849, 1800 },
        { 1850, 1850 }, { 2425, 2400 }, { 2999, 2950 }, { 3000, 3000 },
        { 3001, 3000 }, { 4999, 3000 }, { 5000, 3000 },
    };
    static const struct { unsigned int requested, apdo, expected; } pps_intersections[] = {
        { 3000, 3000, 3000 }, { 3000, 1850, 1850 },
        { 3000, 1799, 0 }, { 5000, 5000, 3000 },
        { 1700, 3000, 0 }, { 2999, 1999, 1950 },
    };
    static const struct {
        unsigned int source_min, source_max, expected_min, expected_max;
    } voltage_intersections[] = {
        /* Linux 7.2 reports active source APDO bounds. Clamp those to the
         * X810 sink/pump envelope (5.0..10.5 V). */
        { 5000, 11000, 5000, 10500 },
        { 3300, 11000, 5000, 10500 },
        { 5000, 10500, 5000, 10500 },
        { 7000, 10500, 7000, 10500 },
        { 5010, 10000, 5010, 10000 },
        /* Disjoint/malformed ranges must fail closed. */
        { 3300, 4999, 0, 0 },
        { 10501, 21000, 0, 0 },
        { 11000, 3300, 0, 0 },
        { 0, 0, 0, 0 },
        { 10500, 10499, 0, 0 },
        { 0xffffffffU, 0xffffffffU, 0, 0 },
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
    for (i = 0; i < sizeof(pps_intersections) / sizeof(pps_intersections[0]); i++) {
        unsigned int got = x810_pd_pps_intersect_current(
            pps_intersections[i].requested, pps_intersections[i].apdo);
        if (got != pps_intersections[i].expected ||
            (got && got % X810_PPS_CURRENT_STEP_MA)) {
            fprintf(stderr,
                    "PPS intersection request=%u APDO=%u mA: got %u, want %u\n",
                    pps_intersections[i].requested, pps_intersections[i].apdo,
                    got, pps_intersections[i].expected);
            return 1;
        }
    }
    for (i = 0; i < sizeof(voltage_intersections) /
                    sizeof(voltage_intersections[0]); i++) {
        unsigned int min_mv = 0xaaaaU, max_mv = 0xbbbbU;
        unsigned int expected_min = voltage_intersections[i].expected_min;
        unsigned int expected_max = voltage_intersections[i].expected_max;
        int got = x810_pd_pps_intersect_voltage(
            voltage_intersections[i].source_min,
            voltage_intersections[i].source_max, &min_mv, &max_mv);
        int expected = expected_max != 0;

        if (got != expected ||
            (got && (min_mv != expected_min || max_mv != expected_max)) ||
            (!got && (min_mv != 0xaaaaU || max_mv != 0xbbbbU))) {
            fprintf(stderr,
                    "PPS voltage board clamp source=%u..%u mV: got %d (%u..%u), want %d (%u..%u)\n",
                    voltage_intersections[i].source_min,
                    voltage_intersections[i].source_max, got, min_mv,
                    max_mv, expected,
                    voltage_intersections[i].expected_min,
                    voltage_intersections[i].expected_max);
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
    header = HEADER.read_text()
    dts = DTS.read_text()

    def define_value(name: str) -> int:
        match = re.search(rf"^#define\s+{name}\s+(\d+)U?\s*$", header, re.M)
        assert match, f"missing numeric define {name}"
        return int(match.group(1))

    pps_match = re.search(
        r"PDO_PPS_APDO\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", dts
    )
    assert pps_match, "X810 Type-C node must advertise its PPS sink APDO"
    sink_min_mv, sink_max_mv, sink_max_ma = map(int, pps_match.groups())
    assert define_value("X810_PPS_MIN_MV") == sink_min_mv == 5000
    assert define_value("X810_PPS_MAX_MV") == sink_max_mv == 10500
    assert define_value("X810_PPS_MAX_MA") == sink_max_ma == 3000
    assert define_value("X810_PPS_DEFAULT_MA") <= sink_max_ma
    assert define_value("X810_PPS_MIN_MA") <= sink_max_ma
    assert define_value("X810_PPS_MAX_MV") % 20 == 0
    assert define_value("X810_PPS_MAX_MA") % 50 == 0
    assert "SM5440_OPERATING_SNK_MW\t\t15000" in DIRECT_DRIVER.read_text()
    direct = DIRECT_DRIVER.read_text()
    headroom_match = re.search(
        r"^#define\s+SM5440_EXTRA_HEADROOM_MV\s+(\d+)\s*$", direct, re.M
    )
    resistance_match = re.search(
        r"^#define\s+SM5440_R_TTL_MILLIOHM\s+(\d+)\s*$", direct, re.M
    )
    assert headroom_match and resistance_match, "missing SM5440 PPS headroom tuning"
    extra_mv = int(headroom_match.group(1))
    r_ttl_mohm = int(resistance_match.group(1))

    def target_mv(current_ma: int, battery_mv: int) -> int:
        # Keep this calculation in step with sm5440_pps_target_mv(): current
        # dependent cable drop + fixed startup margin, rounded up to 20 mV.
        headroom = (current_ma * r_ttl_mohm + 999) // 1000 + extra_mv
        pump = ((2 * battery_mv + headroom + 19) // 20) * 20
        power_floor = ((15000 * 1000 // current_ma + 19) // 20) * 20
        return max(pump, power_floor, 8200)

    # The live 1.8 A attempt requested 9.38 V with a 4.31 V pack, then
    # measured 8.80 V at the pump before REVBLK.  The X910's empirically
    # stable SM5440 setting was ~1100 mV initial headroom.  This candidate
    # gives 1101 mV and requests 9.74 V, while the worst allowed 3 A / 4.35 V
    # point stays inside this board's 10.5 V sink ceiling.
    assert extra_mv == 525, "normal PPS startup margin should reflect measured REVBLK evidence"
    assert target_mv(1800, 4310) == 9740
    assert target_mv(3000, 4350) == 10200
    assert 2 * 4350 < target_mv(3000, 4350) <= sink_max_mv
    # A brief on-device PPS run (2026-10-13) reached an ADC VBAT sample of
    # 3908 mV while the battery current was about 4.3 A. The input side was
    # sampled near 2.1 A; normal PPS still requests its configured 3 A ceiling.
    # Validate the candidate's representative setpoint against the X810's
    # 10.5 V sink limit. Arithmetic does not prove transient stability.
    live_vbat_mv, live_target_ma = 3908, 3000
    live_target_mv = target_mv(live_target_ma, live_vbat_mv)
    assert live_target_mv == 9320, (
        f"recent live-state candidate arithmetic changed: {live_target_mv} mV"
    )
    assert 2 * live_vbat_mv < live_target_mv <= sink_max_mv
    assert "mv > max_uv / 1000" in function_body(
        direct, "static int sm5440_negotiate_pps("
    ), "PPS target must be rejected when source/board APDO cannot supply it"
    assert "op-sink-microwatt = <15000000>" in dts
    assert re.findall(r"PDO_FIXED\s*\(\s*(\d+)\s*,\s*(\d+)", dts)[:2] == [
        ("5000", "3000"), ("9000", "3000")
    ], "fixed-PDO advertisement must match the documented 5/9 V, 3 A limits"
    assert "default/max 3000" in DIRECT_DRIVER.read_text(), (
        "kernel parameter documentation must reflect the board's 3 A cap"
    )
    check_charger_arbitration()
    print("X810 PD/PPS limits and charger-owner handoff invariants passed")


if __name__ == "__main__":
    main()
