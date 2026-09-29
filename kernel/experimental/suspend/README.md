# Experimental SM-X810 suspend candidate

This directory is deliberately excluded from the default kernel patch glob.
The candidate is **not** an established X810 fix and must not be included in a
release until it has passed supervised physical suspend/wake testing.

The detailed evidence and source/IRQ audit are in
[`docs/x810-research/SUSPEND-WAKE.md`](../../../docs/x810-research/SUSPEND-WAKE.md).

## Evidence and scope

The X810's persisted journal contains an actual failed deep-suspend attempt:
on 2026-09-27 at 12:19:03 +07 it ends immediately after `PM: suspend entry
(deep)`, with no logged resume before the next boot. Other recorded attempts
show `Wakeup pending. Abort CPU freeze`, followed by an immediate s2idle
entry/exit. Read-only live inspection found `mem_sleep=s2idle [deep]`, the
PMIC power-key wake interface enabled, and the FTS touchscreen IRQ exposed as
a wake source; `/sys/power/pm_wakeup_irq` was 193 (`fts1ba90a`). That is
consistent with at least one working touchscreen wake path, but does not
explain the hung deep-suspend attempt.

The sibling [SM-X910 Ubuntu port](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra)
has an IPCC-summary-IRQ patch in `kernel/patches/ipcc-mask-summary-during-suspend-gts9u.patch`.
Its analysis attributes spurious suspend returns to ADSP SMP2P traffic on the
APSS IPCC IRQ, which upstream requests with `IRQF_NO_SUSPEND`. The X810 and
X910 both use SM8550, Qualcomm IPCC, and the same mainline `qcom-ipcc` driver,
so the narrow IRQ-flag change is a reasonable **candidate**, not a proven
cross-model fix. The board compatible differs: X810 is
`samsung,gts9pwifi`; the sibling patch targets `samsung,gts9uwifi` and cannot
be copied verbatim.

The X910 repo's [current hardware-status table](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra/blob/main/docs/hardware-status.md)
still rates deep suspend as measured/uncertain and records later cycles that
ended without a logged exit. Its older suspend/NPU notes describe UFS PHY
resume failures, but the X810 failure captured here has no UFS error lines
because the log stops at suspend entry. For that reason this candidate does
**not** alter QMP/UFS PHY sequencing or storage power settings. The separate
UFS/QMP candidate is deferred until the missing resume trace can be captured
without risking the installed root filesystem.

The pinned Linux 7.2 IRQ core masks ordinary non-wakeup IRQs during suspend
and reenables them at resume. The IPCC summary interrupt is level-high in the
SM8550 DT, and its receive register is explicitly cleared only by the handler;
this makes deferred service after resume plausible, but receive-state retention
has not been confirmed on X810 hardware. X910 traces support an IPCC-related
false-return fix, while later X910 deep-suspend hangs remained; the candidate
must not be advertised as a general suspend repair.

## Building the candidate (never part of a default release)

After preparing the exact pinned Linux 7.2.0 source tree, opt in explicitly:

```sh
X810_SUSPEND_EXPERIMENTAL=1 bash kernel/prepare.sh /path/to/linux-7.2.0
```

The only experimental patch currently staged is
`ipcc-mask-summary-x810.patch`. It removes `IRQF_NO_SUSPEND` from the IPCC
summary IRQ only when Linux identifies `samsung,gts9pwifi`; all other boards
retain upstream behavior. Linux 7.2's `of_machine_is_compatible()` searches
the complete root compatible list (`include/linux/of.h` and
`drivers/of/base.c`), so the X810-specific value matches despite the fallback
compatible strings. It defers mailbox delivery while the system is suspended;
it does not disable the touchscreen, power-key or other configured wake
sources. The default build leaves the kernel unchanged.

Do not test by suspending the normal UFS-root installation unattended. A
physical test needs a supervised recovery path and should record kernel
`suspend_stats`, wake IRQ, IPCC/ADSP events, UFS PHY/UIC errors, and root
filesystem state after each short wake cycle. Passing one cycle is not
acceptance; repeat deep and s2idle cycles and verify storage reads/writes.
