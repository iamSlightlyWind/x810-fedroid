# CYG1 SM5714 bypass source audit

The Samsung source archive `SAMFW.COM_SM-X810_XSP_X810XXS5CYG1_fa/Kernel.zip`
contains an SM5714 charger bypass implementation, but it does not establish a
safe Fedora control for operating the tablet from the adapter instead of the
battery.

In `SM-X810_15_Opensource.zip` → `Kernel.tar.gz`,
`kernel_platform/msm-kernel/drivers/battery/charger/sm5714_charger/sm5714_charger.c`
parses `battery,ovp_bypass_mode` and routes `chg_set_en_bypass_mode()` through
one of two Samsung-specific paths:

- With `ovp_bypass_mode`, it sets the SM5714 to suspend mode and calls
  `sec_pd_manual_jig_ctrl()` to drive the PDIC JIGON state.
- Otherwise, it changes the charger FACTORY1 bypass bit, forces a 1 A input
  current limit, changes AICL/reverse-blocking behavior, and calls a Samsung
  fuel-gauge extended property.

The entry points are Samsung `POWER_SUPPLY_EXT_PROP_*` operations and a private
debug attribute, not a generic Linux `charge_type` interface. The source
archive does not include an X810 product DTS confirming whether
`battery,ovp_bypass_mode` is enabled. The generic SM5714 DTSI does not set it.
The related X816B DTS archive is a different product variant and cannot fill
that gap.

Therefore this source is evidence of vendor-specific SM5714 modes, not enough
to determine the electrical meaning, required board configuration, or safe
runtime sequencing of the requested adapter-powered bypass behavior. Do not
port its register writes or expose them as a user toggle without matching
product DTS evidence and supervised hardware validation. Fedora's charge cap
is implemented separately through the standard power-supply start/end
threshold properties; this does not enable bypass. The threshold driver
defaults to start 0/end 100 (no cap). If userspace writes only an end value
below 100, the driver derives a 10-point lower restart threshold, matching
Samsung's normal store-mode hysteresis; an explicit start-threshold write
takes precedence.

## Standard charge-limit candidate

The kernel source now contains opt-in `POWER_SUPPLY_PROP_CHARGE_CONTROL_START_THRESHOLD`
and `...END_THRESHOLD` support for the battery power supply. It uses the
battery's reported capacity and coordinates the SM5714 switching charger with
the SM5440 direct-charge owner so the pump is handed back before Q4 is opened.
It is intentionally disabled by default (`0`/`100`), and values are
runtime-only; nothing is persisted or exposed in GNOME/Tab Companion yet.
Offline policy tests and compilation of both driver objects passed. The
currently running tablet is still on the unmodified kernel: its
`/sys/class/power_supply/sm5714-battery/` has no `charge_control_*_threshold`
files. This is therefore not yet a user-usable or on-device-validated charge
limit. It must be built into a new kernel and tested with a reversible cap
before being described as working. It is separate from bypass and leaves the
live charging state untouched.
