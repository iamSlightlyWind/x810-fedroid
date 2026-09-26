# Explicit STM32 pogo keyboard recovery

The X810 EF-DX815 uses the same STM32 pogo controller path as Samsung's Tab S9
Ultra port. Rebinding the I²C driver fixes the intermittent keyboard because
probe enters STM32 system-boot mode using BOOT0/NRST, reads the immutable ID and
flash version, and then resets the MCU into its application. It does not flash
firmware. A physical detach only cuts the accessory VDDO rail; the STM32 is on
the tablet's I²C/MCU side and that rail cut does not reliably reset it.

This source patch adds a privileged, explicit `recover` sysfs control that runs
the same read-only bootloader handshake and restarts the application without
unbinding the driver or rebuilding the input device. It releases any held key
or touch contacts, masks both IRQ paths during reset, verifies the application
handshake, then restores IRQ processing and rechecks the physical connection.
No firmware write, automatic watchdog, partition operation, or device change is
performed by applying this overlay.

The patch also restarts the STM32 application after a physical disconnect/reconnect
edge: disconnect immediately cuts VDDO as before, and reconnect performs the
documented NRST pulse after power-up but before data IRQs are re-enabled. Samsung's
vendor driver explicitly controls NRST on cover close/open; VDDO cycling alone
does not reliably reset this controller. This should remove the dependence on
waiting for the accessory rail to discharge, but requires testing on the tablet.
It does not solve a spontaneous silent stall that has no I2C error, IRQ, or
connection edge; an idle keyboard produces no events either, so an inactivity
watchdog cannot safely distinguish failure from normal idle.

## Apply / use

Apply to the local Ubuntu Tab S9 port source before building its kernel:

```sh
kernel-overlay/pogo-recovery/apply.sh \
  work/upstream/ubuntu-galaxy-tab-s9-ultra
```

After building and installing a kernel containing the patch, recover the
currently connected keyboard with:

```sh
sudo sh -c 'echo 1 > /sys/bus/i2c/devices/10-002a/recover'
```

This requires root and briefly resets the keyboard MCU, so it drops held keys
and touch contacts. It is a manual recovery action; an automatic timeout-based
reset is intentionally not included because an idle keyboard is normal and the
driver cannot infer that a user is attempting to type.

## Validation

The patch is source-only. The changed driver object was compiled successfully
against the local Linux 7.2.0 arm64 configured tree. Installing or exercising
it on the tablet requires a later kernel build/install and explicit device
testing; applying this patch does not touch the live tablet.

An **unpromoted candidate** that holds NRST through physical detach is kept
separately at [`candidates/README.md`](candidates/README.md). It is not applied
by `apply.sh`; do not replace the baseline until a rapid unplug/replug failure
is measured on-device.
