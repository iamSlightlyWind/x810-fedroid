#!/usr/bin/env python3
"""Offline tests for PPS refresh deadline arithmetic and error handling."""

from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "kernel/files/x810_sm5440_bypass_policy.h"
DRIVER = ROOT / "kernel/files/sm5440_direct.c"


def test_fake_clock_and_fault_paths() -> None:
    source = r'''#include <assert.h>
#include <stdbool.h>
#include <limits.h>
#include "x810_sm5440_bypass_policy.h"

/* Model the driver's fail-closed I2C check boundary for fault injection. */
static int rearm_model(int status2_read, int status3_read, int latch_read,
                       unsigned int status2, unsigned int status3,
                       unsigned int int1, unsigned int int2,
                       unsigned int int3, unsigned int int4, int *pump_on)
{
    if (status2_read < 0)
        return status2_read;
    if (status3_read < 0)
        return status3_read;
    if (latch_read < 0)
        return latch_read;
    if (x810_sm5440_pt_rearm_faulted(status2, status3, int1, int2,
                                     int3, int4))
        return -5;
    *pump_on = 1;
    return 0;
}

int main(void)
{
    const unsigned long interval = X810_SM5440_PPS_REFRESH_INTERVAL_MS;
    const unsigned long poll = 1000;
    unsigned long last = 10000, now, delay, accepted;
    int pump_parked, accepted_request, pump_on;

    assert(interval == 2500);
    assert(!x810_sm5440_pps_refresh_due(last + interval - 1, last,
                                        interval));
    assert(x810_sm5440_pps_refresh_due(last + interval, last, interval));
    assert(x810_sm5440_pps_refresh_delay(last + 500, last, interval,
                                         poll) == 1000);
    assert(x810_sm5440_pps_refresh_delay(last + 1500, last, interval,
                                         poll) == 1000);
    assert(x810_sm5440_pps_refresh_delay(last + 2300, last, interval,
                                         poll) == 200);
    assert(x810_sm5440_pps_refresh_delay(last + interval, last, interval,
                                         poll) == 0);

    /* Simulate the previous PPS request followed by the unchanged park,
     * synchronous request, ADC-settle and pump-rearm tail. Deadline-based
     * scheduling removes accumulated tick/poll delay; maximum TCPM retries are
     * bounded to four 200 ms sleeps per property (voltage + current).
     */
    for (now = last + 100; now <= last + 1900; now += 300) {
        unsigned long work_tail = now - last;
        delay = x810_sm5440_pps_refresh_delay(now, last, interval, poll);
        accepted = now + delay + 2 * 4 * 200;
        assert(accepted - last <= interval + 1600);
        assert(accepted - last < 5000); /* measured source fallback window */
        assert(delay == (work_tail >= interval ? 0 :
                         (interval - work_tail < poll ?
                          interval - work_tail : poll)));
    }

    /* A failed TCPM call is not a successful request: preserve the old
     * deadline. The existing failure path leaves the pump parked and invokes
     * verified fixed-PD handoff; do not model/reorder its safety sequence.
     */
    last = 700;
    now = last + interval;
    pump_parked = 1;
    accepted_request = 0; /* e.g. all five bounded attempts return -EAGAIN */
    if (accepted_request)
        last = now;
    assert(pump_parked);
    assert(last == 700);
    assert(x810_sm5440_pps_refresh_due(now, last, interval));

    /* unsigned subtraction remains valid across jiffies-like wrap. */
    last = ULONG_MAX - 999;
    now = last + interval;
    assert(x810_sm5440_pps_refresh_due(now, last, interval));
    assert(!x810_sm5440_pps_refresh_due(now - 1, last, interval));

    /* Both live thermal status and read-to-clear fault latches inhibit a
     * re-arm; an all-clear fresh sample alone permits it. */
    assert(!x810_sm5440_pt_rearm_faulted(0, 0, 0, 0, 0, 0));
    assert(x810_sm5440_pt_rearm_faulted(0x02, 0, 0, 0, 0, 0));
    assert(x810_sm5440_pt_rearm_faulted(0, 0x08, 0, 0, 0, 0));
    assert(x810_sm5440_pt_rearm_faulted(0, 0, 0, 0, 0x80, 0));
    pump_on = 0;
    assert(rearm_model(0, 0, 0, 0, 0, 0, 0, 0, 0, &pump_on) == 0);
    assert(pump_on);
    pump_on = 0;
    assert(rearm_model(-5, 0, 0, 0, 0, 0, 0, 0, 0, &pump_on) == -5);
    assert(!pump_on);
    assert(rearm_model(0, -5, 0, 0, 0, 0, 0, 0, 0, &pump_on) == -5);
    assert(!pump_on);
    assert(rearm_model(0, 0, -5, 0, 0, 0, 0, 0, 0, &pump_on) == -5);
    assert(!pump_on);
    assert(rearm_model(0, 0, 0, 0x02, 0, 0, 0, 0, 0, &pump_on) == -5);
    assert(!pump_on);
    assert(rearm_model(0, 0, 0, 0, 0, 0, 0, 0, 0x80, &pump_on) == -5);
    assert(!pump_on);
    return 0;
}
'''
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        raise RuntimeError("need a C compiler to run the offline policy test")
    with tempfile.TemporaryDirectory(prefix="x810-pps-timing-") as tmp:
        c_file = Path(tmp) / "test.c"
        binary = Path(tmp) / "test"
        c_file.write_text(source)
        subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(POLICY.parent), str(c_file), "-o",
                        str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


def test_driver_anchors_only_success_and_keeps_park_sequence() -> None:
    driver = DRIVER.read_text()
    policy = POLICY.read_text()
    assert "SM5440_REFRESH_TICKS" not in driver
    assert "pps_ticks" not in driver
    assert "x810_sm5440_pps_refresh_due(jiffies, sm->last_pps_request" in driver
    assert "x810_sm5440_pps_refresh_delay(jiffies" in driver
    assert "sm->last_pps_request = jiffies;" in driver
    refresh = driver[driver.index("static int sm5440_refresh_pps("):]
    refresh = refresh[:refresh.index("\nstatic int ", 1)]
    request = refresh.index("sm5440_psy_retry(sm, POWER_SUPPLY_PROP_CURRENT_NOW")
    timestamp = refresh.index("sm->last_pps_request = jiffies;", request)
    assert request < timestamp
    assert "if (!ret)" in refresh[:timestamp]
    rearm = driver[driver.rindex("static int sm5440_pt_rearm_check("):]
    rearm = rearm[:rearm.index("\nstatic int ", 1)]
    for text in ("SM5440_REG_STATUS2", "SM5440_REG_STATUS3",
                 "sm5440_pt_read_fault_latches(sm, latches)",
                 "x810_sm5440_pt_rearm_faulted", "if (ret)",
                 "return -EIO"):
        assert text in rearm
    renegotiate = driver[driver.index("static int sm5440_renegotiate_pps("):]
    renegotiate = renegotiate[:renegotiate.index("\nstatic void ", 1)]
    order = [renegotiate.index("sm5440_pump_off(sm)"),
             renegotiate.index("sm5440_refresh_pps(sm)"),
             renegotiate.index("sm5440_wait_vbus_settled(sm, sm->target_mv)"),
             renegotiate.index("sm5440_pump_on(sm)")]
    assert order == sorted(order)
    assert renegotiate.count("sm5440_pt_rearm_check(sm,") == 2
    assert "X810_SM5440_PPS_REFRESH_INTERVAL_MS\t2500UL" in policy


if __name__ == "__main__":
    test_fake_clock_and_fault_paths()
    test_driver_anchors_only_success_and_keeps_park_sequence()
    print("PPS refresh deadline/timing tests passed")
