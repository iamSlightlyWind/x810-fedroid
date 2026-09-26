> **Status update:** this draft began before Linux boot. Fedora now boots on the physical X810 and the boot set is dual-boot switched; unresolved X810-specific findings and recovery caveats remain below. See `linux-ports-docs/x810-fedora.md` for current status.

# Boot strategy — research draft only

Fedora has since booted and runs on the physical X810; the notes below retain
the dated bring-up history and unresolved gates. A booted system does not make
a new image safe to write. This file separates sibling prior art from X810
facts; never transplant X910 boot behavior without exact X810 validation.

## Live X810 facts

- A single Android boot-image partition set appears in the live GPT: `boot`
  (100,663,296 bytes), `init_boot` (8,388,608), `vendor_boot`
  (100,663,296), `dtbo` (16,777,216), and `recovery` (109,576,192).
- `vbmeta` is 131,072 bytes at `/dev/block/sde15` on UFS block device `sde`,
  which currently reports kernel read-only. Do not include it in any future
  OS-transition set; how it could be changed is unproven.
- The running FDT root is a Qualcomm/Kalama MTP DT with
  `qcom,board-id = <0x00010008 0x00000004>` and Samsung model string
  `GTS9PWIFI ... board-id,04`.
- Host-side decoding of the captured `dtbo.img` found an Android DT table with
  three overlays: Samsung GTS9PWIFI board IDs 00, 02 and 04. Overlay 04 has
  `qcom,board-id = <0x00010008 0x00000004>`, matching the live FDT. This
  strongly supports a base-DTB-plus-board-overlay stock arrangement and shows
  the selected board variant; it does **not** prove how ABL will select or
  accept a future mainline DTB/DTBO set.
- Captured `boot.img`, `init_boot.img`, `vendor_boot.img`, `dtbo.img`,
  `vbmeta.img`, `recovery.img`, and `param.img` are stored locally under the
  ignored probe directory and have SHA256 checksums. Host-side inspection found
  boot header v4 (kernel in `boot`, ramdisk in `init_boot`), vendor boot v4
  with one platform ramdisk, vendor cmdline/bootconfig, and an embedded base
  DTB. The base DTB identifies generic Kalama (`qcom,board-id = <0 0>`),
  whereas the live FDT reflects board-specific overlay 04. Keep those artifacts
  and roles distinct. **Update:** matching CYG1 firmware has now been obtained
  and checked. Its stock `vendor_boot.img` and `dtbo.img` are byte-identical to
  live; stock `boot.img`, `init_boot.img`, and `recovery.img` differ from live.
  The current set is therefore not an untouched CYG1 set. Attribute the
  divergence before treating any downloaded stock image as a restore set.
  Owner reports that the TWRP installation instructions required a modified
  `vbmeta`; the specific guide/file and its relation to the captured image have
  not yet been independently matched.
- Captured `vbmeta.img` has AVB algorithm `NONE`, flags `2`, no descriptors,
  and rollback index 0 according to host-side `avbtool`. This is a measured
  image property, not authorization to alter it; the live `sde` block device
  currently reports read-only. Matching CYG1 BL's stock `vbmeta.img` differs:
  it is `SHA256_RSA4096`, flags 0, with AVB chain/hash descriptors. Do not
  replace the live vbmeta with it; preserve the actual live image unchanged.
- Verified-boot state is orange and `ro.boot.flash.locked=0`. These live
  properties and the image-level vbmeta inspection describe the current state;
  they do not establish a safe flashing path.
- TWRP `3.7.1_12-0` is currently reachable by USB ADB as root. Recovery
  command-line identity independently names `SM-X810` and `X810XXS5CYG1` even
  though the generic TWRP property reports `SM-X816B`. Full-partition readback
  from TWRP exactly matches the original `boot`, `init_boot`, `vendor_boot`,
  `dtbo`, `vbmeta`, and `recovery` backups. `param` now differs at nine
  boot/init metadata bytes after the recovery transition; retain the original
  `param` snapshot and never include it in OS switching. **Update:** Download
  Mode was entered from TWRP without writing; Heimdall 2.2.2 detected the
  tablet and read its 99-entry UFS PIT. The parsed PIT and hash-identified raw
  PIT are in ignored `probes/android-baseline/`. This verifies Download Mode
  USB transport and read-only PIT access, not flash/restore behavior; no
  partition write or repartition was attempted. Tablet is back in TWRP.

## X910 prior-art design, inspected at source

The current X910 source uses the following architectural pattern:

1. Android boot-image-v4 chain; build bundle contains `boot`, `init_boot`,
   `vendor_boot`, and `dtbo`.
2. The X910 bundle builder places the board DTB both appended after the kernel
   in `boot` and in the Android v4 `vendor_boot` header; `init_boot` carries
   the generic ramdisk; `vendor_boot` also contains a platform ramdisk fragment,
   cmdline and bootconfig. Its documented ABL path disables runtime DTBO and
   uses a deliberately non-table DTBO to force a fallback. The X910 prose
   describes the effective DTB as coming from `vendor_boot`, while the builder
   comment calls the fallback the appended-DTB path. Preserve that documentation/
   code tension rather than inferring the active copy from filenames alone;
   their behavior is physically validated on X910, not X810.
3. Android and Linux store separate copies of exactly those four transition
   images. `vbmeta` remains stable and is not switched, because changing AVB
   state breaks Android's metadata-encryption assumptions and can force data
   loss/recovery behavior.
4. Rootfs lives on internal UFS. It is installed and read-back hash-verified
   before boot-chain images are written, so a slow rootfs failure does not
   replace the Android set first.
5. The switcher validates stored set completeness/expected sizes, writes raw
   images, fsyncs and hashes each destination read-back, and does not reboot
   on failure. Four writes are not atomic; a mixed set requires restoring a
   complete saved set from TWRP.
6. TWRP is an independent recovery partition and is not in the swapped set.

These findings are sourced to X910 `docs/boot-strategy.md`, `docs/dual-boot.md`,
`scripts/build-android-v4-bundle.sh`, `scripts/swap-boot-set.sh`,
`configs/twrp/repartition-update-binary`, and the current boot-switch helper.
X910's old `sdaN` values and image-size assumptions are not evidence for X810.

## X810-specific gates

1. Capture and hash live X810 boot-chain partitions and the matching full
   firmware; **live capture, initial decode and byte-comparison to CYG1 are
   complete**. Determine why the current `boot`/`init_boot`/`recovery`/`vbmeta`
   differ from CYG1 and establish DT handoff behavior before constructing
   images.
2. Determine whether the live images differ from the exact XSP Android 15
   `X810XXS5CYG1` firmware build (KernelSU, TWRP, AVB changes, etc.). Do not
   overwrite an unexplained working boot set.
3. Establish ABL selector behavior (model/compatible, Qualcomm board/platform
   ID, PMIC and revision) using exact X810 stock images/source. Validate a
   proposed X810 DTB offline before any boot attempt.
4. USB TWRP ADB recovery and Download Mode USB/PIT read are proven. A Fedora
   GTK4 switcher now validates exact X810 partition sizes and image hashes,
   writes only the four boot-set partitions, and verifies readback. A complete
   Fedora boot set is retained under `/var/lib/x810-boot-sets/fedora`.
5. Reconcile the X810 protected memory map and test UFS/root mount and recovery
   before display or optional devices.

The live-image decode was host-only. Android boot header is v4; the boot kernel
is a plain ARM64 Image, init_boot ramdisk is LZ4, and vendor_boot contains an
LZ4 platform ramdisk, vendor cmdline/bootconfig and a generic Kalama base DTB.
The `dtbo` partition is a three-entry Android DT table, not a non-table image.
Do not adopt X910's deliberately non-table-DTBO fallback on the X810 without
proving ABL behavior: the X810 stock image has an ordinary board-selector
table, with a live-matching board-id-04 overlay.

## X810 switcher deployment (2026-09-25)

The Fedora system now has a GTK4 app, **Tab Companion**, adapted from the
X910 project's Linux-side switch UI. It is intentionally X810-specific and
does not use X910 partition indices. It stages a selected set but never
reboots. The helper checks board family, root partition, exact partition
sizes, manifest hashes and each partition readback. Since the four writes are
not atomic, a write error must be repaired before rebooting.

The CYG1 Android set and a readback-verified Fedora set are stored on
`linuxroot` under `/var/lib/x810-boot-sets/`. The Android set was staged and
readback-verified into `boot`, `init_boot`, `vendor_boot`, and `dtbo`; the
tablet was not rebooted at deployment time. `vbmeta`, `super`, `userdata`,
`recovery`, GPT/PIT and other firmware partitions were not written. The next
step is an owner-triggered reboot to test Android's boot on this X810. Until
that test, successful Android boot remains unproven. See
[`TAB-COMPANION.md`](TAB-COMPANION.md) for the app and recovery boundaries.
