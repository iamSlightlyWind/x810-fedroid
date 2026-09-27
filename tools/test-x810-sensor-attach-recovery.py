#!/usr/bin/env python3
"""Exercise X810 SSC recovery policy with deterministic command mocks."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/overlay/usr/libexec/gts9wifi-sensors-resume"
SENSORPD = "hexagonrpcd-adsp-sensorspd.service"


SYSTEMCTL = r"""#!/bin/sh
printf 'SYSTEMCTL %s\n' "$*" >> "$X810_EVENTS"
case "$1" in
    list-jobs)
        exit 0
        ;;
    is-active)
        [ "$(cat "$X810_ACTIVE_FILE")" = 1 ]
        ;;
    start|restart)
        if [ "$2" = "" ]; then
            exit 2
        fi
        if [ "$2" = "hexagonrpcd-adsp-sensorspd.service" ]; then
            printf '1\n' > "$X810_ACTIVE_FILE"
        fi
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
"""

TIMEOUT = r"""#!/bin/sh
if [ "${1-}" = "--kill-after=1" ]; then
    shift
fi
case "${1-}" in
    *s|[0-9]*) shift ;;
esac
exec "$@"
"""

SSCCLI = r"""#!/bin/sh
count=$(cat "$X810_PROBE_FILE")
count=$((count + 1))
printf '%s\n' "$count" > "$X810_PROBE_FILE"
printf 'PROBE %s\n' "$count" >> "$X810_EVENTS"
if [ "$X810_SUCCESS_AT" != never ] && [ "$count" -ge "$X810_SUCCESS_AT" ]; then
    echo 'Accelerometer sensor measurement: mock sample'
    exit 0
fi
echo 'SSC QMI Service not found'
exit 1
"""

SLEEP = r"""#!/bin/sh
printf 'SLEEP %s\n' "$*" >> "$X810_EVENTS"
exit 0
"""


class RecoveryHarness:
    def __init__(self, *, initially_active: bool, success_at: int | str) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="x810-sensor-recovery-")
        self.root = Path(self.tmp.name)
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.events = self.root / "events"
        self.active = self.root / "active"
        self.probes = self.root / "probes"
        self.active.write_text("1\n" if initially_active else "0\n")
        self.probes.write_text("0\n")
        for name, content in (
            ("systemctl", SYSTEMCTL),
            ("timeout", TIMEOUT),
            ("ssccli", SSCCLI),
            ("sleep", SLEEP),
        ):
            path = self.bindir / name
            path.write_text(content)
            path.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bindir}:{self.env['PATH']}",
                "X810_EVENTS": str(self.events),
                "X810_ACTIVE_FILE": str(self.active),
                "X810_PROBE_FILE": str(self.probes),
                "X810_SUCCESS_AT": str(success_at),
            }
        )

    def run_helper(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(HELPER)],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

    def events_list(self) -> list[str]:
        return self.events.read_text().splitlines()

    def close(self) -> None:
        self.tmp.cleanup()


def check(condition: bool, explanation: str) -> None:
    if not condition:
        raise AssertionError(explanation)


def sensorpd_restarts(events: list[str]) -> list[int]:
    return [
        index
        for index, event in enumerate(events)
        if event == f"SYSTEMCTL restart {SENSORPD}"
    ]


def test_active_attachment_is_probed_before_any_restart() -> None:
    harness = RecoveryHarness(initially_active=True, success_at=1)
    try:
        result = harness.run_helper()
        events = harness.events_list()
        probes = [i for i, event in enumerate(events) if event.startswith("PROBE ")]
        check(result.returncode == 0, result.stderr or "helper failed")
        check(not sensorpd_restarts(events), "active sensorspd was restarted before a failed probe")
        check(
            not any(event == f"SYSTEMCTL start {SENSORPD}" for event in events),
            "active sensorspd was redundantly started",
        )
        check(len(probes) == 1, f"expected one successful SSC probe, got {len(probes)}")
        check(
            events.index(f"SYSTEMCTL restart iio-sensor-proxy.service") > probes[0],
            "SensorProxy restarted before SSC returned a sample",
        )
    finally:
        harness.close()


def test_inactive_attachment_is_started_and_settled() -> None:
    harness = RecoveryHarness(initially_active=False, success_at=1)
    try:
        result = harness.run_helper()
        events = harness.events_list()
        start = events.index(f"SYSTEMCTL start {SENSORPD}")
        settle = events.index("SLEEP 10")
        first_probe = events.index("PROBE 1")
        check(result.returncode == 0, result.stderr or "helper failed")
        check(start < settle < first_probe, "fresh sensorspd attach was not settled before probes")
        check(not sensorpd_restarts(events), "initial inactive-start path performed a premature restart")
    finally:
        harness.close()


def test_stale_active_attachment_gets_one_restart_after_five_probes() -> None:
    harness = RecoveryHarness(initially_active=True, success_at=8)
    try:
        result = harness.run_helper()
        events = harness.events_list()
        restarts = sensorpd_restarts(events)
        fifth_probe = events.index("PROBE 5")
        sixth_probe = events.index("PROBE 6")
        check(result.returncode == 0, result.stderr or "helper failed")
        check(len(restarts) == 1, f"expected one sensorspd restart, got {len(restarts)}")
        check(fifth_probe < restarts[0] < sixth_probe, "recovery did not follow five failed SSC probes")
    finally:
        harness.close()


def test_unavailable_ssc_is_bounded_and_never_loops_restarts() -> None:
    harness = RecoveryHarness(initially_active=True, success_at="never")
    try:
        result = harness.run_helper()
        events = harness.events_list()
        checks = [event for event in events if event.startswith("PROBE ")]
        check(result.returncode == 1, "helper should fail when SSC remains unavailable")
        check(len(checks) == 20, f"expected 20 bounded probes, got {len(checks)}")
        check(len(sensorpd_restarts(events)) == 1, "unavailable SSC caused a restart loop")
        check("SSC did not recover" in result.stderr, "missing bounded-failure diagnosis")
    finally:
        harness.close()


def main() -> None:
    test_active_attachment_is_probed_before_any_restart()
    test_inactive_attachment_is_started_and_settled()
    test_stale_active_attachment_gets_one_restart_after_five_probes()
    test_unavailable_ssc_is_bounded_and_never_loops_restarts()
    print("PASS: X810 sensor attach recovery keeps a fresh PD, waits for SSC, and restarts at most once")


if __name__ == "__main__":
    main()
