# X810 EL721 fingerprint status

This is a read-only status audit. No sensor SPI/image data, stored templates,
Android credentials, secure-world provisioning, or fingerprint enrollment was
accessed.

## Current Fedora observation

- Running tablet reports kernel `7.2.0-gts9wifi`.
- `/dev/esfp0`, `/dev/k250a`, and `/dev/tee0` exist; `/dev/qsee_ipc_irq_spss`
  does not.
- `egis_el721` and `qcomtee` are loaded. The SPSS IRQ module is not loaded,
  and no supported secure-owner service is installed/active.
- EL721 sysfs identifies the reader as `X816`. The X910 reference identifies
  its reader as `X916`; the panel exposes `fod_mode`, `fod_ready`, `cell_id`,
  and `fod_circle`, but the panel controls alone do not provide contact
  detection or enrollment.
- X810 touch is ST FTS1BA90A at I2C `11-0049`; the X910 repository's hard-coded
  Goodix `13-005d` FOD/contact interface is absent. Do not copy its touch
  suppression logic or pen-proximity hook as an X810 implementation.
- Fedora still has no EL721 `libfprint` backend or tested fprintd/PAM path.
  Samsung-signed CYG1 TAs and per-device calibration are also not integrated.

## What can be reused, and what cannot

The X910 port is a useful reference for QTEE marshaling, signed split-image TA
loading, protocol tests, and its dedicated libfprint backend. It is not a
drop-in X810 solution: the sensor model, calibration, panel cell geometry,
touch controller/FOD handoff, secure firmware, and persistent SPSS owner
lifetime all need a separately reviewed X810 adaptation. A secure owner may
hold a DMA mapping for the lifetime of the boot; once initialized, killing or
restarting it is not a safe recovery method.

Next safe engineering steps are offline only: rebase the reference backend to
Fedora's libfprint version, add protocol/lifecycle tests, and establish
owner-supplied X810 calibration provenance without publishing proprietary
blobs. Do not start SPSS/QTEE, load its IRQ module, stage TAs, or call secure
credential operations as an exploratory test. A later live test needs a
separate explicit plan covering boot-only ownership, reboot recovery, and no
enrollment/deletion of prints.
