# X810 suspend/wake audit

## Result

Deep suspend is still **unreliable and unvalidated** on the SM-X810. The
available journal shows an attempted deep suspend that never logged a return,
but it does not establish whether the tablet failed to receive a wake event,
the PSCI/platform resume path hung, or a device (including storage) failed
during resume. Do not describe the issue as fixed based on the candidate below.

The only source candidate so far is an opt-in, board-scoped change to the
Qualcomm IPCC summary IRQ. It addresses a plausible spurious-interrupt path;
it does not add or repair a wake source. It remains out of the default kernel
build pending X810 physical validation.

## X810 evidence (read-only)

Persisted kernel journal from the failed boot ends at
`2026-09-27 12:19:03 +07:00` with `PM: suspend entry (deep)`. It contains no
later `PM: suspend exit`, resume trace, UFS error, panic, or orderly shutdown
before the next boot. This is evidence of a failed/unobserved resume, not a
root-cause trace. A different boot has three attempts that log
`Wakeup pending. Abort CPU freeze`, followed by s2idle entry and exit; those
are aborted/very short cycles, not successful deep-suspend validation.

The last available live read-only snapshot showed:

* `/sys/power/mem_sleep`: `s2idle [deep]` (deep selected);
* PMIC power-key and touchscreen devices had `power/wakeup=enabled`;
* `/sys/power/pm_wakeup_irq` was 193, which maps to the FTS1BA90A touchscreen
  in `/proc/interrupts`;
* the touchscreen driver's documented double-tap test previously woke the
  system from one deep-suspend cycle.

Those observations rule out a universally absent touchscreen wake route, but
do not prove the power key woke the failed cycle or that the last snapshot is
the present TWRP/live state. No new on-device query or suspend test was made
for this audit.

## Source-path audit

The port's DTS includes root compatibles
`samsung,gts9pwifi`, `samsung,gts9wifi`, and `qcom,sm8550`. It declares the
FTS touchscreen with `wakeup-source`; the cover switch and volume-up GPIO key
also carry `wakeup-source`. The PMK8550 PON power-key node is enabled. The
shared SM8550 IPCC node in upstream `sm8550.dtsi` has a GIC summary IRQ but no
`wakeup-source` property.

In the pinned Linux 7.2 source, `drivers/mailbox/qcom-ipcc.c` requests its
summary IRQ with `IRQF_TRIGGER_HIGH | IRQF_NO_SUSPEND | IRQF_NO_THREAD`.
`qcom_ipcc_irq_fn()` drains pending client/signal IDs from `IPCC_REG_RECV_ID`,
clears each one via `IPCC_REG_RECV_SIGNAL_CLEAR`, then dispatches the virtual
mailbox IRQ. Probe disables `IPCC_CLEAR_ON_RECV_RD`, so reads do not
intentionally consume the pending signal before the explicit clear. In the
same pinned tree, the SM8550 IPCC parent line is GIC SPI 229, level-high, and
the node does not declare `wakeup-source`. Linux IRQ PM code disables
non-wakeup IRQs during suspend unless requested with `IRQF_NO_SUSPEND`, then
reenables suspended IRQs during resume. Therefore the source-level expectation
is that removing `IRQF_NO_SUSPEND` stops normal IPCC handler execution during
deep sleep and a still-asserted level IRQ will be serviced after resume. This
is an inference from the driver, interrupt core, and DT; no X810 hardware trace
has confirmed that IPCC receive state survives its suspend power state.

Linux's [suspend/interrupt documentation](https://docs.kernel.org/power/suspend-and-interrupts.html)
states that `IRQF_NO_SUSPEND` leaves an IRQ enabled through the suspend
noirq phases, but does not itself make it a system wake IRQ. That makes this
flag worth auditing: unsolicited ADSP/IPCC traffic can continue reaching the
summary handler during suspend, even though the IPCC node is not declared as
a wake source. The sibling
[SM-X910 port's IPCC patch](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra/blob/main/kernel/patches/ipcc-mask-summary-during-suspend-gts9u.patch)
reports ADSP SMP2P traffic through IPCC causing an unexpected PSCI return and
removes `IRQF_NO_SUSPEND` only on that board. The sibling's
[development notes](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra/blob/main/docs/development-notes.md#ipcc-traffic-must-not-wake-the-cpu-during-system-suspend)
describe supporting X910 traces (IPCC/ADSP IRQs immediately following an
IRQ-less PSCI return, then test cycles remaining asleep until PMIC power-key
IRQ 21) and subsequent X910 cycles ending at deep-suspend entry without an
exit. This supports the narrow false-return mechanism on X910, but also shows
the IPCC change is not a universal cure for all suspend hangs, even on that
model. It is a lead for X810, not proof of its root cause. Its current
[hardware status](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra/blob/main/docs/hardware-status.md)
still rates deep suspend as uncertain.

## Candidate and exact scope

`kernel/experimental/suspend/ipcc-mask-summary-x810.patch` removes
`IRQF_NO_SUSPEND` only when `of_machine_is_compatible("samsung,gts9pwifi")`
is true. In Linux 7.2, this helper checks the root DT's whole `compatible`
property (`include/linux/of.h` → `drivers/of/base.c`), so the X810-specific
string is matched even though `samsung,gts9wifi` and `qcom,sm8550` are also
listed as fallback compatibles. It preserves the existing flag set on every
other machine. The sibling patch targets
`samsung,gts9uwifi`, so copying its condition unchanged would silently miss
the X810.

The candidate is applied only if `X810_SUSPEND_EXPERIMENTAL=1` is explicitly
set during `kernel/prepare.sh`; the normal `kernel/patches/*.patch` glob and
release workflow do not include it. Masking this summary IRQ should leave
pending IPCC state for the IRQ to drain after normal resume, but that is a
source-level expectation only: Linux does not show whether the receive state is
retained through X810's power collapse. A mailbox client requiring immediate
service during suspend is a possible regression. Therefore do not promote it
into default/release kernels before supervised physical testing.

No QMP/UFS PHY patch was added. The sibling port documents a UFS PHY
`-110`/resume failure on its SM-X910 and has investigated QMP reset sequencing,
but the X810 journal available here contains no UFS error. Applying an
unverified PHY/storage change without that evidence would risk the installed
root filesystem and would not explain the missing resume trace by itself.

## Offline validation

The official CDN archive
`https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.tar.xz` was downloaded
for this audit. Its SHA-256 matched the repository workflow pin exactly:
`f9fef3d14c0df53819026f4be74459835c2a0b0dcbf5b5bbd9ea19f0829402b3`.
The candidate applied to clean Linux 7.2.0 with `git apply --check` and zero
fuzz; `tools/test-x810-suspend-candidate.py --kernel-tree <clean-source>`
passed. The patched `drivers/mailbox/qcom-ipcc.o` compiled successfully for
AArch64 with Clang/LLVM. This proves source compatibility/buildability only,
not IRQ behavior or suspend/resume reliability on hardware.

## Required physical validation before promotion

Use a supervised session with an independently recoverable boot path. Before
each cycle capture the boot ID, `mem_sleep`, wakeup-enabled devices,
`/sys/kernel/debug/wakeup_sources`, `/proc/interrupts`, and
`/sys/power/suspend_stats/*`; also preserve a remote console/journal path that
does not disappear when Wi-Fi suspends. Exercise wake with the power key and
touch separately. After every cycle confirm a new `PM: suspend exit`, expected
wake IRQ, IPCC pending signal handling, successful UFS reads/writes, and a
writable root. Repeat both deep and s2idle cycles. If the tablet again becomes
unreachable with no exit trace, recover only with the owner's supervision and
inspect the previous-boot logs before changing another subsystem.
