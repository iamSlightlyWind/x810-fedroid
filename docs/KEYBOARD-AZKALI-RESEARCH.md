# EF-DX815 keyboard: Azkali / Samsung driver comparison

## Sources inspected

- The Azkali Tab S9 kernel subproject is cloned at
  `/home/slightlywind/Repositories/azkali-samsung/kernel-samsung-gts9wifi`,
  branch `android13-5.15-halium`. Its Git remote is
  `https://gitlab.com/azkali-samsung/gts9/ubports/kernel-samsung-gts9wifi.git`.
- The closest public Tab S9 Ultra port is cloned at
  `/home/slightlywind/Repositories/azkali-samsung/ubuntu-touch-galaxy-tab-s9-ultra`;
  its README reports the EF-DX920 keyboard, not the X810's EF-DX815.
- The local Samsung X810 CYG1 source/device-tree dump identifies EF-DX815 as
  STM32 model `0xfb`, supplies the shared `keyboard_stm/stm32_gts9family.bin`,
  and declares the MCU NRST GPIO. This is the device-specific evidence; the
  Ubuntu Touch repo is family-level reference only.

## Relevant upstream behavior

The cloned Azkali kernel is `android13-5.15-halium` at `d249ce0a7`. Its normal
physical-detach path and keyboard-cover flip notifier are distinct:

* In `stm32_pogo_i2c.c`, `stm32_conn_isr()` immediately disables accessory
  VDDO on a GPIO62-low edge, then schedules connection reconciliation after
  250 ms. `stm32_check_conn_work()` restores VDDO if the connection signal has
  already returned high by that check; it does not run NRST or the full
  keyboard-start handshake for this short-pulse case. If the low state persists,
  the subsequent high transition invokes `stm32_keyboard_connect()`.
* In `stm32_pogo_notifier_v3.c`, a separate cover-flip close cancels connection,
  init, and IC work; masks IRQs; notifies touch/key state; asserts MCU NRST;
  and disables VDDO. Cover-open reenables IRQs, releases NRST, then waits in
  20 ms steps (up to 80 attempts) for the controller's enabled state. That is
  **not** the default action on the physical GPIO62 detach edge.
* In `stm32_pogo_fn_v3.c`, `stm32_power_reset()` pulses NRST for 3 ms and waits
  10 ms, but this helper is used by runtime I²C error recovery rather than the
  ordinary short unplug/replug path.

The existing held-NRST candidate is therefore a plausible way to make a short
physical cycle start from a known state, but it is not a literal copy of
Samsung's physical hot-unplug sequence. Source comparison cannot prove it will
fix the observed delayed-vs-immediate reconnect behavior. A read-only sample of
the currently running tablet found GPIO62 IRQ 189 at count 168 and registered
EF-DX815 keyboard/touchpad input nodes, but did not capture a failing reconnect; a
diagnostic sample during the failure is still needed to prove whether the edge
was seen.

## Branch/history scope and measured mainline clue

The clone now also has `origin/master` (`bc1d436df`, 2026-07-06). Its pogo
driver and gts9wifi DTS are identical to `android13-5.15-halium`
(`d249ce0a7`, 2026-07-25); the path history is effectively the initial vendor
import plus a permissions-only fix, so Azkali provides no later alternate
debounce/reset implementation to backport. The model/protocol evidence still
applies to EF-DX815: vendor code treats model `0xfb` as a bypass keyboard and
decodes the same 16-bit key/press format as the X810 driver. X910's DTS shares
GPIO13 NRST, GPIO62 connection, GPIO75 active-low DATA, and IRQ polarities.

Separately, the locally cloned Ultra Linux-port history includes an empirical
[GPIO62-glitch fix (`ce4a9fc`)](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra/commit/ce4a9fc485efd6b5c81caa1b40256e5f7c76a1f6), later reverted while isolating a firmware-version
problem (`b9ba7ea`). Its trace showed short low pulses while physically
attached; a 3–4 ms threaded-handler re-read avoided cycling VDDO for a pulse
that had already returned high. This directly motivates the additional,
unpromoted `candidates/0002-ignore-brief-connection-glitches.patch`; it counts
ignored pulses in read-only diagnostics. It is a safe host-build candidate, not
a verified fix for this X810's longer unplug/replug delay.

## X810 recovery changes

The recovery implementation adds a privileged controller reset action, NRST
recovery on a failed I²C read, and hold-NRST handling through disconnect. The
owner confirmed that this cumulative recovery build worked on the tablet
yesterday. The 0002 GPIO62-glitch guard remains a separate, unconfirmed
experiment and is not enabled. The fresh-install Fedora kernel did not include
any pogo driver or DT node; this turn integrates the confirmed recovery code
with the X810 SE15/GPIO10 DT and EF-DX815 model, host-builds it, and publishes
the source. It still requires manual kernel/DTB flashing and user verification.

Tab Companion Fedora release
[`Tab Companion build #19`](https://github.com/iamSlightlyWind/tab-companion/releases/tag/tab-companion-build-36331825449)
(`1.4.2.19`) ships an optional, authenticated “Reset keyboard controller”
action. It narrowly verifies the connected
EF-DX815, rebinds only I2C device `10-002a`, and reports whether the controller
reinitialized. It does not access key events, write controller firmware, or
touch partitions. Its helper logic is unit-tested against a synthetic sysfs
tree (three tests pass); this proves helper behavior, not sustained hardware
reliability. A live driver test still requires installing the app and
exercising it on the tablet.

## Remaining validation

1. Install the deferred-I²C-recovery test ZIP manually from TWRP, keeping its
   known-good boot-only restore ZIP accessible there.
2. Boot Fedora and compare rapid reconnect with a >5-second detach; verify the
   keyboard continues through repeated key/touchpad use and check the recovery
   log/counters. Do not record key values.
3. Keep the previous boot set available until multiple cycles pass. Only then
   promote the patch from candidate to normal kernel overlay.

No boot partitions were written as part of this work.

## Latest runtime I2C failure and source-only candidate

The rapid-detach test yielded a few key strokes, then failed again. During a
failure, the event reader returned `-ENXIO` without the driver's `; application
reset` suffix; `recoveries=0` while the keyboard input node remained registered.
The source confirms that the reset is gated by `powered && GPIO62 > 0` (the
registered `attached` state alone does not imply either). Diagnostics from the
exact error instant did not include both gate values, so the record identifies
the skipped gate but not whether VDDO state or GPIO62 was low.

CYG1 source (`work/firmware-cyg1/samsung-source/.../stm32_pogo_i2c.c`) retries
an application read three times and resets after a negative exhausted result;
it short-circuits when the connection-state/lock gate recognizes a disconnect.
Its reset is a 3 ms NRST assertion then 10 ms settle. The separate Fedora
candidate `candidates/0003-defer-read-recovery.patch` preserves a safe
power/connection gate: when it cannot pulse immediately, it keeps a reset
request and queues reconciliation. The worker now performs the NRST pulse only
after confirmed power plus GPIO62-high, including the case where the input
remains registered; it retries a raced reset without recurring work while
actually detached. The pulse is serialized against power-off by `power_lock`.

Patch application and arm64 object compilation passed in
`out/keyboard-i2c-recovery-candidate/`; original source/object/command-file
hashes were restored. A full kernel was then built in a reflinked source copy;
the `Image.gz` passed gzip validation, the reconstructed boot image passed
AVB verification, and both the candidate and known-good restore ZIPs passed
archive, size, SHA-256, and boot-only updater contract checks. Artifacts and
instructions are in `out/keyboard-i2c-recovery-build/artifacts/`. The owner
later reported that the newer held-NRST + 0003+0004+0005 recovery package
worked on the tablet; this earlier 0003-only artifact is not the exact build
covered by that report.

## Diagnostic-only follow-up

The latest “few keypresses then stops” observation cannot by itself show whether
the keyboard stopped requesting service, the I²C transaction failed, or Linux
received events that were not delivered to the input stack. The 0003 deferred
recovery candidate addresses the observed `-ENXIO` with a suppressed immediate
reset, but cannot repair a missing GPIO75 interrupt or an input/decoder issue.

A separate, no-key-values `candidates/0004-diagnostics-only.patch` applies after
0003. It records error-time `attached`, `powered`, GPIO62 connection, GPIO75
DATA, `data_irq_enabled`, reset-pending, and input registration; adds event-read
error and recovery-gate counters/last errno; and removes the previous
`last_key` value from the diagnostics output. It does not alter interrupt,
power, reset, or event-handling policy. The ARM64 object build and patch apply
passed; the kernel source/object/build command file were restored. Its output
and hashes are in `out/keyboard-diagnostics-candidate/`.

On a future reproduced failure, compare the diagnostics before and after one
harmless keypress. A flat data-IRQ counter points to GPIO75/IRQ delivery;
IRQ/deassert increases point to pulse level/timing; rising read-error/retry
counts with `-ENXIO` point to STM32 I²C/controller response; rising key-event
counts without visible typing point downstream at input registration or event
delivery. No hardware result is available yet.

## Invalid event after rebind: 0004/0005 candidates

The user then reported that a no-reboot driver rebind briefly restored input,
then it stopped again; probe immediately saw an invalid key event followed by an
I2C `-ENXIO`. Samsung CYG1 and Azkali use a 16-bit value with 15-bit key code
and one press bit. Their v2 key parser updates its small state array only for
codes below `STM32_KEY_MAX`, but still calls `input_report_key()` for each code;
it does not reset the controller on an out-of-range code. Therefore the raw
protocol alone does not prove such an event is a corrupted frame.

Revised `candidates/0004-diagnostics-only.patch` never exposes key contents:
it removes the last-key sysfs value and the raw invalid-event log argument,
replacing that log with a generic message and `invalid_key_events` counter.
`candidates/0005-recover-invalid-key-event.patch` is a separate inference: when
the Fedora parser rejects code 0 or a code above Linux `KEY_MAX`, it returns
`-EPROTO`, letting 0003 use the already-gated reset/defer path. This matches
the invalid-event-then-`-ENXIO` observation. The owner later confirmed the
held-NRST + 0003+0004+0005 build worked on-device. It cannot help when GPIO75
never interrupts or the STM32 is silent.

Host artifacts are separated and boot-only:
- `out/keyboard-combined-build/artifacts/` is 0003+0004 diagnostics.
- `out/keyboard-invalid-frame-build/artifacts/` is 0003+0004+0005.
Both packages have AVB-verified 100663296-byte boot images, pass the single-boot
ZIP contract tests, and include byte-identical known-good boot restore ZIPs.
The owner-confirmed report applies to the held-NRST + 0003+0004+0005 package;
the 0003+0004-only package is not covered. The build source is isolated; no
tablet write, push, flash, or reboot occurred here.
