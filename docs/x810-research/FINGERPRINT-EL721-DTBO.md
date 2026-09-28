# X810 EL721 stock GPIO binding

## Scope

This is only the Linux-side mapping needed to power the optical reader on the
SM-X810 CYG1 board-id 04. It does **not** enable Linux SPI access, biometric
authentication, enrollment, or GNOME/PAM integration. The latter remain
separate secure-world and userspace problems.

## Source evidence

The official X810 CYG1 AP contains `vendor_boot.img.lz4`. Its vendor ramdisk
includes Samsung's `fingerprint.ko` (metadata: “Samsung Electronics Inc. EL7XX
driver”, GPL, CYG1 kernel vermagic). The local, read-only audit of that module
showed:

- `el7xx_probe_common` obtains the GPIO described by `etspi-sleepPin` using
  `of_get_named_gpio_flags`.
- `el7xx_power_control` raises that sleep GPIO during the power-on sequence
  after the regulator step; power-off and reset lower it, and reset raises it
  again. Thus this is the sensor's enable/reset line, not an LDO-enable line.
- The matching CYG1 board-id 04 DTBO entry identifies the sensor as
  `etspi,el7xx` / EL721 / X816, sets `etspi-sleepPin = <&tlmm 0x9b 0>` (TLMM
  GPIO155, active high), names its supply `VDD_BTP_3P3`, and has **no**
  `etspi-ldoPin` property.

The checked-in `tools/fixtures/x810-cyg1-board04-el721-dtbo-fixture.dts` is a minimal
non-flashable fixture of those relevant DT properties; it is not Samsung's
binary DTBO or a replacement overlay. No Samsung firmware/module binary or
trustlet is included in this repository or release artifacts.

The local extracted CYG1 `samsung-r04-compiled.dtb` independently confirms
root `qcom,board-id = <0x10008 0x04>` and the EL721 overlay values above. The
GPIO mapping therefore keys on the tablet board/revision and stock compatible,
not merely on the existence of a similarly named fingerprint node.

## Source change and limits

`kernel/files/egis_el721.c` now maps the verified TLMM GPIO155 to the driver's
normal `enable` connection with a device-name-keyed `gpiod_lookup_table`, only
when the live root board ID is `0x10008/4` and the stock `etspi,el7xx` node has
`etspi-sleepPin`. Mainline 7.2 does not expose Samsung's 5.15
`devm_gpiod_get_from_of_node()` helper, so the portable descriptor lookup API
is used instead. The mapping is registered lazily during an explicit sensor
power request and removed with device teardown; it neither requests nor drives
the GPIO during module probe. Other board revisions, generic `enable-gpios`
devices, and the synthetic fallback keep their previous lookup path. This
change deliberately does not infer or drive GPIO91 / `etspi-ldoPin`.

The same lazy power path creates a PM8550B LDO2 regulator mapping. A source
audit found that the previous implementation attempted to attach a synthetic
OF node with `device_add_of_node()` even when the stock platform device already
had its own DT node; Linux 7.2 rejects that replacement with `-EBUSY`, leaving
the `vdd` consumer without the new supply reference. The current candidate
instead adds `vdd-supply` to the existing stock node in the same reversible OF
changeset as the late regulator, retaining synthetic-node attachment only for
the no-OF fallback device. Module teardown now unbinds the consumer before
withdrawing that regulator/changeset. This is source/API-correctness work, not
evidence that the regulator voltage or fingerprint authentication has been
validated on-device.

Source contract/fixture check:

```sh
python3 tools/test-x810-fingerprint-kernel-contract.py -v
```

The source/test result does not prove that the updated kernel has been
installed or exercised on a tablet. The candidate compiles as an external
module against the local Linux `7.2.0-gts9wifi` build tree with its Clang
toolchain (`ARCH=arm64 LLVM=1 W=1`); compilation does not exercise GPIO
ownership, regulator registration, TrustZone SPI, or enrollment. On-device
validation is still required before considering the low-level reader usable.
Never test this mapping by issuing raw SPI or exposing fingerprint frames.
Authentication remains unavailable unless the per-device signed secure-world
path is independently validated; do not alter Keymaster/HwVault credentials or
provision secure state as a workaround.
