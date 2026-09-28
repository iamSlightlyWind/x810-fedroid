#!/usr/bin/env python3
"""Check that the X810 camera snapshot stays read-only and diagnoses layers."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/diagnose-x810-camera.sh"
DRIVER = ROOT / "kernel/files/hi1337_gts9u.c"
DOC = ROOT / "docs/x810-research/CAMERA-INTERMITTENT.md"


def check_diagnostic_contract() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "/sys/firmware/devicetree/base" in script
    assert "hynix,hi1337-gts9u-rear" in script
    assert "hynix,hi1337-gts9u-front" in script
    assert "status" in script and '"$node/reg"' in script
    assert 'cam -l' in script and "LIBCAMERA_LOG_LEVELS='*:DEBUG'" in script
    assert "dmesg --ctime" in script, "dmesg should be a fallback when journal access is denied"
    assert "--capture" not in script and "--stream-to" not in script
    assert "i2cset" not in script and "systemctl restart" not in script


def check_source_supports_hypothesis() -> None:
    source = DRIVER.read_text(encoding="utf-8")
    start = source.index("static int hi1337_probe(struct i2c_client *client)")
    end = source.index("static void hi1337_remove(struct i2c_client *client)", start)
    probe = source[start:end]
    assert probe.count("hi1337_identify(sensor)") == 1
    assert "if (ret)\n\t\tgoto power_off;" in probe

    doc = DOC.read_text(encoding="utf-8")
    assert "not evidence that" in doc
    assert "ordinary I2C identity error is not itself a deferred-probe request" in doc


def check_cli() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    help_result = subprocess.run(
        [str(SCRIPT), "--help"], check=True, capture_output=True, text=True
    )
    assert "Usage: diagnose-x810-camera.sh LABEL" in help_result.stdout
    invalid = subprocess.run(
        [str(SCRIPT), "bad label"], capture_output=True, text=True
    )
    assert invalid.returncode == 2


def check_mocked_snapshot() -> None:
    # Run a complete snapshot with fake camera/media/service tools so the test
    # never probes or streams from a real camera. Sysfs reads remain read-only.
    with tempfile.TemporaryDirectory(prefix="x810-camera-diagnostic-test-") as temp:
        base = Path(temp)
        fakebin = base / "bin"
        fakebin.mkdir()
        commands = {
            "cam": "printf 'mock camera enumeration\\n'\nprintf 'mock libcamera debug marker\\n' > \"$LIBCAMERA_LOG_FILE\"\n",
            "journalctl": "case \"$*\" in *--quiet*) [ \"${MOCK_JOURNAL_ACCESS:-readable}\" = readable ]; exit $?;; *--user*) echo 'mock user journal';; *warning..alert*) echo 'mock kernel warning';; *) echo 'HI1337 identity reads failed: mock';; esac\n",
            "dmesg": "echo 'HI1337 dmesg fallback marker'\n",
            "systemctl": "echo 'mock service status'\n",
            "wpctl": "echo 'mock PipeWire status'\n",
            "v4l2-ctl": "echo 'mock V4L2 inventory'\n",
            "media-ctl": "echo 'mock media topology'\n",
        }
        for name, body in commands.items():
            command = fakebin / name
            command.write_text("#!/bin/sh\n" + body, encoding="utf-8")
            command.chmod(0o755)

        outdir = base / "out"
        env = os.environ.copy()
        env["PATH"] = f"{fakebin}:{env['PATH']}"
        result = subprocess.run(
            [str(SCRIPT), "mock", str(outdir)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        snapshot = (outdir / "mock.txt").read_text(encoding="utf-8")
        debug_log = (outdir / "mock-libcamera.log").read_text(encoding="utf-8")
        assert "===== Live firmware device-tree camera nodes =====" in snapshot
        assert "mock kernel warning" in snapshot
        assert "HI1337 identity reads failed: mock" in snapshot
        assert "mock camera enumeration" in snapshot
        assert "mock libcamera debug marker" in debug_log
        assert "Saved " + str(outdir / "mock.txt") in result.stdout

        fallback_outdir = base / "fallback-out"
        env["MOCK_JOURNAL_ACCESS"] = "denied"
        fallback = subprocess.run(
            [str(SCRIPT), "fallback", str(fallback_outdir)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        fallback_snapshot = (fallback_outdir / "fallback.txt").read_text(encoding="utf-8")
        assert "using readable dmesg ring buffer" in fallback_snapshot
        assert "HI1337 dmesg fallback marker" in fallback_snapshot


def main() -> None:
    check_diagnostic_contract()
    check_source_supports_hypothesis()
    check_cli()
    check_mocked_snapshot()
    print("PASS: read-only X810 camera diagnostic, journal/dmesg paths, HI1337 probe hypothesis, and CLI contract")


if __name__ == "__main__":
    main()
