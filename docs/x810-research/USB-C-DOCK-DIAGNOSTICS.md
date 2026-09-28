# X810 USB-C / PD dock validation

## Current source-side conclusion

No safe code change for MST-dock charging/power pass-through is justified by
the available evidence. The X810 tree already declares a dual-role Type-C port,
sink-first policy, DP alternate mode, fixed sink PDOs of 5 V/3 A and 9 V/3 A,
and a conservative 5 V/0.9 A source PDO. The optional SM5440 direct-charge
path requests PPS through TCPM with a 3 A default and a 10.5 V / 5 A hard
ceiling; the SM5714/TCPM and SM5440 handoff invariants have a static test in
`tools/test-x810-pd-limits.py`. These contracts are not evidence that a
particular MST hub actually negotiates power and data roles as desired.

The optional SM5440 PPS adapter-cap search now steps in 50 mA increments,
matching TCPM's PPS RDO granularity, so it does not skip a legal APDO ceiling
such as 1.85 A. The 1.8 A minimum, 3 A default, 5 A absolute current cap, and
10.5 V voltage cap are unchanged. This is a source-tested compatibility
refinement only; it neither fixes nor validates MST-dock power pass-through.

The local Samsung GPL source at
`/home/slightlywind/Repositories/fedroid/work/firmware-cyg1/samsung-source`
contains the SM5714 PDIC, SM5714 charger and SM5440 vendor drivers and the
`gts9pwifi` device tree. Its input archive is the X810 CYD9 source package
(`SM-X810_15_Opensource_X810XXU5CYD9...`), not an exact CYG1 kernel source
drop. Its PDIC node enables PD role swap and its board DTS provides SM5440
charger configuration. It is a useful near-version register/sequencing
reference, not proof of MST pass-through behavior in Linux. The locally
available Tab S9 Ultra reference documents measured USB-PD/PPS charging and
USB host operation with hubs, and separately measured DisplayPort output; it
does not report a combined MST-plus-external-charger pass-through test.

Until a capture shows where a role swap or contract fails, changing PDOs,
raising current limits, or poking controller registers would be speculation.
In particular, do not exceed the checked-in fixed-PD/PPS ceilings or use raw
I2C register tools as a diagnostic.

## Capture a reproducible comparison

Run the same read-only capture in each state; use descriptive labels and keep
the JSON files together:

```sh
python3 tools/diagnose-x810-usbc.py --label bare-tablet > /tmp/bare.json
python3 tools/diagnose-x810-usbc.py --label pd-charger-only > /tmp/charger.json
python3 tools/diagnose-x810-usbc.py --label mst-dock-with-pd > /tmp/mst-pd.json
python3 tools/diagnose-x810-usbc.py --label mst-dock-no-pd > /tmp/mst-no-pd.json
```

For one capture per attached state, add `--include-tcpm-log` to include the
Type-C/PD controller's debugfs event log:

```sh
python3 tools/diagnose-x810-usbc.py --label mst-dock-with-pd --include-tcpm-log > /tmp/mst-pd.json
```

This is optional and read-only; the tool will not mount debugfs. Reading a
`/sys/kernel/debug/usb/tcpm-*/log` file advances that controller's buffered
log, so collect it **once per state** and preserve the JSON instead of
re-running it to inspect the log. If debugfs is not already mounted or the log
is inaccessible, the report records that fact and continues without changing
the system.

For the powered-dock case, connect the dock's host lead to the tablet, its
external PD input to the dock, and a DP display to the dock. Allow about 15
seconds for negotiation/enumeration before each capture. The four snapshots
record Type-C power/data roles and alternate modes, power-supply telemetry,
DRM connector status, USB topology, and filtered current-boot kernel messages.
`journalctl`/`dmesg` log access may require running the command with `sudo` to
get complete logs; the tool itself never invokes `sudo`.

Compare the Type-C roles and reported voltage/current across the charger-only
and powered-dock cases, then check whether a USB hub and DP connector appear.
The output is diagnostic evidence, not proof that current measured at the
battery is equal to PD input power. Do not include unrelated full logs when
sharing the capture.

## Tool contract and local test

`tools/diagnose-x810-usbc.py` reads only a short allowlist of sysfs/procfs text
attributes; optionally it runs `lsusb -t` and reads filtered current-boot
kernel log lines. It makes no sysfs writes, controller resets, role changes,
PD requests, raw I2C transactions, or device-tree changes.

Run its fixture regression with:

```sh
python3 tools/test-x810-usbc-diagnostic.py
```
