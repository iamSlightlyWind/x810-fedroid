# Unpromoted candidate: hold NRST across physical hot-unplug

`../patches/0001-add-explicit-samsung-pogo-recovery.patch` is the baseline.
It pulses NRST after VDDO returns. The candidate patch here is an **incremental,
not-yet-device-tested alternative**: on a GPIO62 disconnect edge it asserts
NRST before cutting VDDO, then restores VDDO and waits 50 ms before releasing
NRST; it waits another 150 ms before re-enabling the data IRQ. This mirrors
Samsung CYG1's `support_open_close` flip-close/open ordering. CYG1's ordinary
physical detach itself only disables the IRQ and VDDO (`stm32_pogo_i2c.c:1144-61`);
its attach/start waits 50 ms then enables the IRQ (`:1108-29`). The reset hold
occurs on a separate cover-flip-close path (`stm32_pogo_notifier_v3.c:133-50`):
NRST low before VDDO off, then NRST high on open followed by readiness polling.
Applying that sequence to physical detach is therefore a testable inference,
not a demonstrated fix.
The reconnect hunk retries after the normal 250 ms debounce if the connection
GPIO falls again between VDDO restoration and NRST release, so it does not
leave the data IRQ disabled after a raced disconnect. This is a correctness
guard, not a timing workaround.
The candidate is not applied by normal `apply.sh` and must not be treated as
the default fix.

## Incremental connection-glitch guard

`0002-ignore-brief-connection-glitches.patch` is a separate incremental
candidate applied **after** the held-NRST candidate. It samples GPIO62 again
after 3–4 ms, and only asserts NRST/cuts VDDO if the line remains low. A
transient low that rebounds is counted as `connection_glitches` in the
read-only diagnostics and does not touch power/reset. This runs in the threaded
IRQ, so the sleepable GPIO sample is valid. It is based on the measured X910
mainline driver change `ce4a9fc` (2026-08-04): its trace saw GPIO62 drop while
the keyboard remained physically attached (`connected=1` by reconciliation),
causing unintended rail cycling, model `0x00`, and no I²C errors. That guard
was later reverted in `b9ba7ea` while isolating the separate V34/V37 firmware
problem; it is useful evidence for a glitch class, not proof this is the cause
on X810. The port keeps it unpromoted pending the X810 GPIO62 measurement.

The patch check and host kernel-object build must be run against the exact
candidate base, in addition to the existing host image/package checks. Never
present the older `out/keyboard-candidate/boot.img` as containing this
incremental patch.

Host-only validation on 2026-09-26: `git apply --check` and a scratch-tree
apply/source-order assertion passed. The exact Fedora Linux 7.2
`samsung_stm32_pogo.o` target compiled with Clang/LLVM using the saved port
kernel config. Candidate object SHA-256:
`4e3a5c8f793e7f092494be45242db234b8af121855f726e978171efe7c30accf`.
The original source, object and `.cmd` were restored and their hashes checked;
the prior known-good candidate boot image remains
`a09f886613f7af1f644d64ae88270e2715d225c5113f51f15248586a52e6979e` and does
**not** contain this incremental guard. No full Image.gz/boot image was
relinked for the new hunk, and no image was sent to the tablet.

Polarity is checked against the Fedora DT consumer: `reset-gpios` is active-low
and acquired as `GPIOD_OUT_LOW`; therefore logical `1` asserts NRST and logical
`0` releases it. This matches Samsung's raw GPIO sequence (`mcu_nrst=0` on close,
`mcu_nrst=1` on open). The Tab S9 Ultra DT source uses GPIO13 active-low, and a
read-only tablet DT check also showed GPIO13/active-low, GPIO62 connection, and
GPIO75 active-low DATA (`reset-gpios` flags cell `1`). The candidate changes no
firmware, flash, or partition
state. It serializes NRST/VDDO operations with `power_lock`, checks power and
connection before reset release, and leaves the baseline manual recovery
control unchanged.

Apply only to a **throwaway/build** source tree that already has the baseline.
For the Ubuntu-style overlay tree, use `git apply --check` followed by
`git apply` from the tree root. For Fedora/mainline, rewrite only the patch
path as the baseline helper does:

```sh
sed 's#kernel/drivers/samsung_stm32_pogo.c#drivers/input/keyboard/samsung_stm32_pogo.c#g' \
  kernel-overlay/pogo-recovery/candidates/0001-hold-nrst-across-hot-unplug.patch \
  | git -C /path/to/throwaway-linux-tree apply --check -
```

Only after the check succeeds, replace `--check` with a separate apply command.

## Runtime measurement before promotion

Capture one failed rapid unplug/replug and one successful >5 s unplug/replug
with a logic analyzer/scope on **GPIO62 (connection), GPIO75 (DATA/IRQ), VDDO,
and I²C SDA/SCL**, including one harmless keypress after each attach. This
single synchronized capture distinguishes: GPIO62 failing to return to the
attached state; VDDO failing to drop/restore; DATA never asserting; and DATA
asserting while the STM32 I²C transaction NACKs/fails. Preserve the driver's
read-only diagnostics and timestamped `event read failed`/I²C logs with it.
Do not promote based only on the >5 s correlation.

## Host validation

The candidate patch applies to the local Fedora Linux 7.2.0 driver and its
`samsung_stm32_pogo.o` compiles with the configured arm64 Clang toolchain. An
earlier version also built a full `Image.gz`; this updated retry hunk has not
been relinked into a full image. Hardware behavior is unverified; do not flash
or reboot for this candidate without an explicit validation step.

## Deferred recovery after runtime I2C read failure

`0003-defer-read-recovery.patch` addresses the latest observed rapid-detach
failure: the device remained registered, an event read returned `-ENXIO`, but
the log lacked `; application reset` and `recoveries=0`. The existing Fedora
IRQ thread reset only when both `powered` and GPIO62-high were true; with
`attached` still true, an edge/power race could suppress recovery while leaving
the registered input node intact. The failure log did not snapshot those two
gate values at the same instant, so it cannot prove which one was false.

This candidate follows stock CYG1 semantics: `stm32_i2c_read_bulk()` retries
failed application reads three times, returns early as disconnected only when
its connection state/lock gate says the accessory is detached, and otherwise
calls `stm32_power_reset()` for an exhausted negative result (non-bootloader
address). The reset is a 3 ms NRST pulse followed by 10 ms settle. Fedora keeps
its safety gate: if power/connection is not valid, it records reset intent and
queues the existing connection worker instead of toggling NRST while VDDO is
off or GPIO62 is low. The worker now consumes pending reset intent even if the
input remains attached and the rail has already returned, and retries a raced
reset. The pulse is serialized with detach's power-off sequence by
`power_lock`; the connection IRQ releases that lock before taking the protocol
lock, preserving the existing lock order. A truly detached keyboard is
reconciled once and does not cause a timer loop; reset intent remains for the
next attach.

Host-only patch application, arm64 object build, and full `Image.gz` link
passed. A partition-sized boot image was AVB-verified and packaged with the
boot-only TWRP updater; both it and the separate known-good restore ZIP pass
host archive/hash/size tests. Artifacts and manual test order are in
`out/keyboard-i2c-recovery-build/artifacts/`. This candidate is **not
physically validated** and remains separate from the held-NRST (`0001`) and
GPIO62 glitch (`0002`) candidates. Keep unpromoted until a live test confirms
rapid reconnect plus sustained key input after the deferred NRST recovery.

## No-key-values runtime diagnostics

`0004-diagnostics-only.patch` applies after `0003` and changes no GPIO, reset,
power, or input handling. On each failed event read it snapshots attachment,
VDDO state, GPIO62 connection, GPIO75 data-ready, data IRQ enabled state,
reset-pending flag, and input registration; it also counts event-read failures
and recovery-gate skips and records the errno. The sysfs diagnostics gain the
same state/counters and no longer expose the old `last_key` value. It does not
log key values or packet payloads. The candidate is specifically useful for
classifying the next “a few keys then silence” report; it is not itself a fix.

Interpret counters only after a harmless test key:

- No `data_irq` increase: hardware DATA/GPIO75 or IRQ delivery is suspect.
- IRQ increases with `data_irq_deasserted`: the sampled DATA line was inactive
  by hard-IRQ time; inspect GPIO75 pulse/polarity/timing.
- `event_read_errors` / `read_retry_releases` increase (especially `-ENXIO`):
  event transaction/controller state failed; inspect the captured power/attach
  gates and I²C lines. `0003` should report whether recovery was deferred.
- `key_events` increases while UI input does not: investigate input-device
  registration/evdev delivery or event parsing, not the IRQ link.

Host-only patch application (after 0003) and arm64 object compilation passed;
`out/keyboard-diagnostics-candidate/` contains the object, source snapshots,
restoration hashes, and test log. No image/device operation was performed.

## Invalid event after rebind: no-key-value diagnostics and recovery

The user observed that an unbind/bind briefly restored input, then the driver
saw an invalid event and an I2C `-ENXIO`. Revised `0004-diagnostics-only.patch`
removes the last-key sysfs field and the invalid-event log's raw value; it emits
a generic message plus an `invalid_key_events` counter only.

`0005-recover-invalid-key-event.patch` is a separate candidate after 0003+0004:
if the Fedora parser rejects code 0 or a code above `KEY_MAX`, it returns
`-EPROTO` so 0003 can reset immediately when power/connection gates are valid,
or defer safely otherwise. CYG1/Azkali decode a 16-bit key/press field and call
`input_report_key` even for values beyond their `STM32_KEY_MAX` state array;
they do not reset on an invalid code. Thus 0005 is a recovery inference from
the observed invalid-event-then-`-ENXIO` sequence, not a protocol-proven fix.
It cannot address silence without a GPIO75 interrupt. Keep it unpromoted until
physical testing shows a sustained recovery.

Host full-boot TWRP candidates (not flashed):
- `out/keyboard-combined-build/artifacts/`: 0003+0004.
- `out/keyboard-invalid-frame-build/artifacts/`: 0003+0004+0005.
Both contain only a boot writer and exact known-good restore ZIP, with AVB,
readback geometry, and ZIP contract validation recorded in their manifests.
