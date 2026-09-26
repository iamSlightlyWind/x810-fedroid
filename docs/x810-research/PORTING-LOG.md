# Porting log

## 2026-09-24 — Initial read-only baseline

- Host project started from `chatgpt_plan.txt`; no existing project source tree.
- Tablet state before test: One UI running; wireless ADB already connected at
  `192.168.1.11:5555`. No reboot, flash, mount, remount, or device-side write
  was attempted.
- ADB also listed an unrelated Pixel XL (`127.0.0.1:15897`); all device
  commands were explicitly pinned to the SM-X810 serial.
- `adb shell getprop`, `su -c id`, kernel state, block layout, bus/input
  enumeration, logs, memory maps and the live device-tree tar were captured by
  `tools/probe-android.sh` under the git-ignored `probes/android-baseline/`.
- `su` works and reports root in a KernelSU SELinux domain (`u:r:ksu:s0`);
  SELinux is enforcing. This establishes Android-side root availability, not
  its durability or suitability for a switcher.
- Identity observed: `SM-X810`, `gts9pwifi`, `gts9pwifixx`, arm64, Qualcomm
  `SM8550` / `kalama` (`ro.vendor.qti.soc_id=519`). Build fingerprint is
  `samsung/gts9pwifixx/gts9pwifi:15/AP3A.240905.015.A2/X810XXS5CYG1:user/release-keys`;
  Android 15 / API 35; security patch 2025-07-01.
- Bootloader string `X810XXS5CYG1` and firmware suffix `S5` support bootloader
  revision 5. `ro.boot.verifiedbootstate=orange`, `ro.boot.flash.locked=0`,
  and `sys.oem_unlock_allowed=1` are consistent with an unlocked state. No
  actual Download Mode/TWRP recovery test has been done (intentionally deferred
  because the owner is using the tablet and said not to restart it).
- Device exposes dynamic partitions and no slot suffix; `/dev/block/by-name`
  has a single `boot`/`init_boot`/`vendor_boot` naming set. These are strong
  indicators of a non-A/B boot arrangement; confirm against GPT/firmware.
- Live PCI function `17cb:1103`, subsystem `17cb:0108`, driver `cnss_pci` was
  observed. Linux's ath11k PCI table maps `17cb:1103` to WCN6855; this is a
  high-confidence WLAN-family identification, but not proof of firmware/BDF
  compatibility. Do not follow the X910 WCN7850/ath12k path unless new X810
  evidence warrants it.
- Live FDT root has `compatible = qcom,kalama, qcom,kalama-mtp, qcom,mtp`,
  `model = "Samsung GTS9PWIFI PROJECT (board-id,04)"`, and downstream
  `qcom,board-id = <0x00010008 0x00000004>`. Boot cmdline identifies
  `GTS9P_ANA38407_AMSA24VU05` / LCD ID `800005`. These are observations of the
  running stock kernel/FDT, not yet the Samsung ABL selector contract for a
  mainline DTB.
- Logs identify `fts_touch` at I²C address `0x49` (bus 59 in that kernel's
  numbering) and `wacom_w90xx` at `0x56` (bus 58). The controller-family
  identification is measured; Linux bus numbers/GPIOs/regulators need DTS
  correlation before reuse.
- Main user LUN reports `sda` with 249,102,336 512-byte sectors and partitions
  through `sda34`; live mount/property data show `userdata` on `sda34`. This is
  now corroborated by read-only 1 MiB captures at each end of sda. Raw GPT
  parsing validates both header CRCs and the complete entry-array CRC: 4096-byte
  GPT sectors, 62,275,584 LBAs, usable range 6–62,275,574, user-data disk GUID
  `98101b32-bbe2-4bf2-a06e-2bb33d000c20`; the full label/geometry table is in
  `docs/PARTITION-LAYOUT.md`. `userdata` begins at LBA 3,568,128 and ends at
  62,275,574; `super` remains separate and occupied. Follow-up reads mapped all
  six UFS LUNs sda–sdf and captured the first/last MiB of each; all captured
  GPTs passed header/array CRC checks. These are not full-device or full-GPT
  backups. All have 4096-byte logical blocks and repeat the same disk GUID;
  observed partition unique-GUID fields equal their type GUIDs. Preserve this
  unusual OEM convention rather than normalizing it.
- UFS block devices sdb and sde report `blockdev --getro=1`; in particular,
  `vbmeta` is `/dev/block/sde15` within read-only sde. Others (sda, sdc, sdd,
  sdf) currently report writable. This is a live kernel observation, not proof
  that Download Mode/TWRP can or cannot flash each partition.
- The Android `sgdisk` binary rejected `-p`; no write-capable option was used.
  `lpdump /dev/block/by-name/super` succeeded read-only and confirms a
  two-slot metadata layout for logical `system`, `odm`, `product`, `system_dlkm`,
  `system_ext`, `vendor`, and `vendor_dlkm` inside `super`; do not repurpose it.
- Live FDT review reveals numerous explicit secure/remoteproc reservations,
  including Gunyah, QTEE, MPSS, CDSP, ADSP/SLPI, SPSS/SPU and TrustUI ranges,
  plus Samsung diagnostic pools. A preliminary subset and provenance notes
  are in `docs/MEMORY-MAP.md`; they are not yet validated against
  `/proc/iomem` or exact stock source. This elevates reserved-memory
  reconciliation to a hard safety gate, not an optional later cleanup.
- **Probe-script failure retained:** while adding full read-only boot-chain
  captures and repeatable GPT edge capture, the first repeated probe completed
  image copies but the GPT parser rejected sda's backup-header window. Diagnosis:
  Android's 32-bit `mksh` arithmetic overflowed when computing the tail skip
  from the 255 GB LUN (`skip` became 409344 instead of 62275328). The raw read
  was still read-only and the tablet stayed connected/running; no device state
  changed. The probe now computes the 64-bit byte/sector math on the host and
  validates all six LUN GPTs. The corrected run passed all six GPTs and
  captured the full live `boot`, `init_boot`, `vendor_boot`, `dtbo`, `vbmeta`,
  `recovery`, and `param` partitions (exact live partition sizes; per-image
  SHA256 manifest in the ignored capture). A second host-side manifest step
  then hit `sha256sum: live-boot-images is a directory`; it did not affect
  captured bytes and is fixed by skipping directories. This failure is also
  retained. No device write or reboot occurred.
- Next: finish prior-art code/history review; compare captured X810 boot images
  with exact matching firmware (candidate identifiers known, archive not yet
  obtained); decode ABL and memory evidence; establish host-controlled recovery
  access only after the owner permits a restart. No Linux boot experiment is
  authorized or underway.

## 2026-09-24 — Live boot image decode (host only)

- Parsed the checksummed copies under ignored `probes/android-baseline/` using
  Android boot-image tools, `dtc`, and `avbtool`. This work touched only host
  files; no ADB write or restart occurred.
- `boot.img` is header v4 with a 44,685,824-byte plain ARM64 Linux Image and no
  ramdisk; `init_boot.img` is v4 with a 2,254,251-byte LZ4 ramdisk. Both report
  Android OS version 13.0 and July 2025 patch metadata (distinct from the
  Android 15 userspace build).
- `vendor_boot.img` is v4, 4096-byte page size, one 13,364,341-byte LZ4
  platform ramdisk, vendor cmdline and bootconfig. It embeds a 1,803,659-byte
  generic Qualcomm Kalama base DTB with `qcom,board-id = <0 0>`. This is not
  the runtime FDT: the latter is board-id 04.
- `dtbo.img` is an Android DT table (three entries, total table size
  `0x24e177`), with Samsung GTS9PWIFI overlays board-id 00, 02 and 04. Entry
  04 has `qcom,board-id = <0x10008 4>` and matches the live FDT model and tuple.
  This supports a stock board-overlay path but does not establish ABL's exact
  selector rules for a future alternate image.
- Captured `vbmeta.img` is AVB algorithm NONE, flags 2, no descriptors, rollback
  index 0. Preserve this exact captured image; the live underlying `sde` LUN
  reports kernel read-only. Recovery has an Android header v2, gzip ramdisk,
  and recovery-DTBO area. These observations are not write/recovery tests.
- No install bundle, partition switcher, Linux boot, firmware flash, GPT change,
  or device restart was attempted. Next priority: obtain and validate the exact
  XSP `X810XXS5CYG1` stock archive, compare boot artifacts, and source the
  matching Samsung bootloader/DT sources and memory map.

## 2026-09-24 — Authorized TWRP USB recovery inspection (read-only)

- After the owner said the tablet was no longer in use and authorized recovery
  access, it was placed in recovery and connected over USB. Host ADB sees one device at serial
  `R52W7082ELA`; every command below was pinned to that serial. No reboot,
  flash, format, wipe, partition write, remount, or userdata decryption was
  performed during this inspection.
- TWRP ADB shell runs as root. Its generic recovery properties say
  `SM-X816B` / `gts9p`, but the bootloader-supplied command line identifies
  `androidboot.em.model=SM-X810`, `androidboot.bootloader=X810XXS5CYG1`, and
  X810 board/sub-PCB values. Treat the recovery's generic X816B label as a
  recovery build label, not the tablet SKU; the command-line identity matches
  the previously measured X810.
- Recovery is TWRP `3.7.1_12-0`, built on `twrp_gts9p`, using Linux
  `5.15.94-Foldiby-+` (not the CYG1 Android kernel 5.15.153). This verifies
  host-controlled USB ADB access in the current recovery session, not any
  ability to flash particular partitions or recover from a failed boot.
- `/proc/mounts` shows only `/dev/block/sda28` mounted as `/cache`; userdata is
  not mounted/decrypted. All six UFS LUNs and by-name links are visible. Device
  sizes and partition links are consistent with the previously captured GPT
  (`userdata` remains `/dev/block/sda34`; `vbmeta` remains `/dev/block/sde15`).
  TWRP reports `sdb` and `sde` read-only, while `sda`, `sdc`, `sdd`, and `sdf`
  report writable. This is status only; no writes were attempted.
- Recovery reports about 11.46 GiB `MemTotal` and the same Qualcomm/Kalama
  platform. Its `/proc/iomem` reservation ranges are live recovery-kernel
  evidence, not a substitute for the Android runtime FDT/source memory-map
  analysis. There is no PCI bus or WLAN netdev in this TWRP boot; that does not
  disprove the One UI observation of PCI `17cb:1103`, since recovery uses a
  distinct kernel/configuration.
- Recovery diagnostics establish the safe USB observation path the prior gate
  required. They do not test download-mode flashing, boot image acceptance,
  a Linux kernel, boot selection, or partitioning. Keep the tablet in recovery
  until the owner asks to resume Android or further authorized work.
- Streamed the seven saved full partition copies from TWRP again and compared
  hashes without saving over the baseline. `boot`, `init_boot`, `vendor_boot`,
  `dtbo`, `vbmeta`, and `recovery` all match their original captured SHA256
  values exactly. `param` differs from its initial image in nine bytes only:
  offset `0x0` (`00` → `02`), offset `0x9001c1` (`30` → `00`), and
  `0x9002f0..0x9002f6` (seven zero bytes → ASCII `init:1 `). These changes are
  consistent with boot/init metadata fields at the known parameter offsets,
  but exact writer/timing is not proven. Keep the original `param.img` backup;
  do not restore or switch `param` as part of an OS boot set. The current
  readback is retained separately under ignored `work/` for comparison only.

## 2026-09-24 — Mainline baseline build and X710 memory-map rejection

- Re-verified the prior-art kernel version at implementation time. The X710
  port's current known-good is Linux 7.2.0; its issue register reports
  WCN6855 MHI/WLAN regression in 7.2.1–7.2.6. Kernel.org lists 7.2.7 as
  current stable (released 2026-09-21), but the X710 repository does not say
  whether that release fixes the issue. To hold the known-good hardware
  baseline constant, use 7.2.0 first and revisit 7.2.7 after X810 boots.
- Downloaded kernel.org `linux-7.2.tar.xz` (SHA256
  `f9fef3d14c0df53819026f4be74459835c2a0b0dcbf5b5bbd9ea19f0829402b3`) and
  verified its detached signature with Greg Kroah-Hartman's published
  kernel.org key fingerprint. Prepared the source with the complete X710 port
  patch set using its pinned `prepare.sh`; all patches applied (some with
  normal offset/fuzz), config fragment merged, and the full arm64 `dtbs`
  target completed successfully on the host. This proves only that the
  upstream port's existing sources/build inputs compile locally.
- Compared the resulting X710 port DTB's 62 nonzero reserved ranges against
  the X810 CYG1 live runtime FDT's 50 ranges using new
  `tools/compare-reserved-memory.py`. Seven measured X810 reservations are
  missing in X710 (UH guest is 6 MiB undersized; TrustUI and six subranges are
  at different addresses). Nineteen X710-only ranges are absent from X810;
  several overlap address ranges X810 reports as `System RAM`. Therefore an
  X710 DTB with only model/board strings changed is rejected for X810. The
  comparison is host-only; no candidate Linux DTB or boot image has been
  flashed or built yet.

## 2026-09-24 — Exact CYG1 firmware and Samsung source acquired

- The owner supplied the download directory
  `/home/slightlywind/Downloads/SAMFW.COM_SM-X810_XSP_X810XXS5CYG1_fa`.
  It contains full AP/BL/CSC/HOME_CSC archives and `Kernel.zip`. All work below
  was host-side; nothing was flashed or sent to the tablet.
- The full downloaded firmware ZIP MD5 is
  `e995aca49b2e17c51cdcfdc0866b297d`, matching the indexed checksum. AP stock
  `dtbo.img` and `vendor_boot.img` are byte-identical to the live copies.
  Stock AP `boot.img`, `init_boot.img`, and `recovery.img` differ. BL's stock
  `vbmeta.img` differs from live: stock is signed `SHA256_RSA4096`, flags 0,
  with descriptors; live is algorithm NONE, flags 2, with no descriptors.
  Important: AP `vbmeta_system.img` is not the main `vbmeta` image; the latter
  is in BL. Do not use the stock vbmeta as a replacement. Exact reasons for
  each live-vs-stock difference remain undetermined; likely root/recovery
  customization is a hypothesis, not yet confirmed from byte-level deltas.
- `Kernel.zip` contains `SM-X810_15_Opensource.zip` (inner SHA256
  `bad7b14888697a4b5c553dd621ee61d1509ef46eff0ebe098a3de7a5b10b0354`; no Git
  commit metadata). It includes Samsung's exact `gts9pwifi_eur_open` target,
  X810 board overlay DTS for revisions 00, 02, 04, and panel source
  `GTS9P_ANA38407_AMSA24VU05`. Samsung downstream kernel baseline is 5.15.153.
  This closes the exact-source discovery gap; it does not make the source a
  mainline DTS or finish the secure-memory/ABL analysis.
- Extracted archive contents remain under git-ignored `work/firmware-cyg1/`;
  raw proprietary source and firmware are not committed. The matching exact
  source now enables high-value board-DT and memory-map comparisons.
- Owner confirms the installed TWRP instructions required modified `vbmeta`.
  This explains the context of the live-vs-stock vbmeta difference; the exact
  TWRP guide revision and matching distributed image still need to be checked.
  KernelSU and TWRP are intentional current customizations, so this is not an
  accidental mismatch or a blocker for research. Keep the captured working
  image set intact; the exact boot/init_boot deltas remain to be classified.
- Offline inspection of the captured live `recovery.img` ramdisk finds TWRP
  binaries (`/system/bin/twrp`, TWRP libraries and `twrp.flags`), confirming
  this captured image is TWRP. Its `init.recovery.usb.rc` configures an ADB
  USB gadget; no Wi-Fi/TCP ADB service was found in the inspected recovery
  init files. The owner's Android-side port-5555 tweak therefore does not
  establish that ADB will remain reachable in TWRP. Do not remotely reboot to
  recovery until a host USB path or physical return path is available. No
  reboot has yet been attempted.
- Compared unpacked CYG1 stock and live boot components on the host. The stock
  and live `boot` kernels differ; the live kernel contains KernelSU strings
  (`KernelSU: ...`), confirming the modified kernel is rooted. The live
  `init_boot` ramdisk contains `init.real`, `kernelsu.ko`, `ksu_block_modules`,
  and `ksu_config`, and its `init` differs from stock. Recovery's live ramdisk
  contains `/system/bin/twrp` and TWRP libraries, confirming the live recovery
  modification. The same CYG1 `vendor_boot` DTB/platform ramdisk and DTBO are
  retained unchanged. Image divergence is now attributed to the owner's
  declared KernelSU/TWRP setup; preserve the exact working set.
- Cross-checked the captured full runtime FDT against exact-source
  `kalama.dtsi` plus the X810 r04 overlay. 49 populated fixed reservation
  tuples match by node/range after board override (notably fragment 103 gives
  `adspslpi_region` its live size). Three runtime-only ranges—`kaslr_region`,
  `uh_heap_region`, `uh_guest_region`—are absent from named source nodes but
  confirmed reserved in `/proc/iomem`; their owner remains unknown, so none
  may be reclaimed. Static map confidence improved; complete ownership/dynamic
  pool analysis and mainline boot safety remain open.
- The exact r04 source independently matches measured FTS1BA90A (I2C `0x49`,
  firmware `tsp_stm/fts1ba90a_gts9p.bin`), Wacom W90xx (`0x56`, firmware
  `wez01_gts9p.bin`), and live panel string `GTS9P_ANA38407_AMSA24VU05`.
  It also declares Goodix GT9916 at `0x5d` for trusted-touch; its active Linux
  role is unresolved. WLAN source declares `qcom,cnss-qca6490`, 32 MiB dynamic
  region and GPIO80 enable, complementing the measured WCN6855 PCI ID and the
  X710 ath11k prior art. None of these source readings is a hardware test.
- Extracted CYG1 `abl.elf` is a stripped 4 MiB ARM32 executable without useful
  DT/board selector strings; offline string inspection did not establish
  ABL's decision algorithm. Stock Android's live FDT and matching board-04
  DTBO entry demonstrate the selected stock overlay, but mainline selector
  acceptance remains an on-device test gate.

## 2026-09-24 — X810 candidate memory audit gate

- Built an ignored, research-only X810-shaped DTB from the sibling X710 DTS by
  replacing its reservation list with measured X810 tuples and exact `no-map`
  flags. Its fixed ranges match exactly, but the stronger audit rejects the
  candidate RAM: the mainline board source has a zero-size `/memory` template
  that relies on ABL patching, while measured X810 live FDT contains eight
  populated RAM ranges. No evidence yet proves CYG1 ABL will patch this new
  X810-targeted tree with those same ranges. This remains a hard stop before a
  boot candidate, not a reason to proceed to flashing.
- The audit now enforces exact `no-map` parity (not merely “do not drop
  measured no-map”), exact root RAM tuples, and no overlapping candidate
  fixed reservations. This caught X710's extra `no-map` flags on splash,
  secure debug pool, pmsg, and Qualcomm reset diagnostics. They are absent in
  the measured X810 runtime tree, so the research-only derivative matched
  those measured attributes rather than copying the sibling settings.

- Follow-up: inserted the exact eight measured X810 RAM tuples into the
  ignored candidate and reran the strengthened audit. It now passes exact
  static-range, no-map, and root-RAM comparisons. This only removes the
  pre-ABL tuple mismatch; it does not verify Samsung ABL's handling and does
  not turn the X710-peripheral derivative into a boot candidate.
- A deeper FDT inventory then identified 19 dynamic `/reserved-memory`
  requests in the live tree. Fifteen active pools request 896 MiB of base
  sizes, none of which are present in the candidate. The four disabled pools
  are distinguished from active requests. Added `tools/list-dynamic-reserved.py`
  to inventory size/alignment/alloc-ranges, status, reusable/no-map flags and
  Samsung `expand_size` without inventing physical placements.
- Mapped representative active references: `cnss_pci0` to WLAN memory, CVP
  to CDSP EVA, DMA heaps to display/QSEE/secure-CDSP/demura/SP/user-contig,
  QMC memshare to QMC DMA, QSEEcom to QSEE pools, and Qualcomm dump/minidump
  users to diagnostic/VA pools. The exact physical placement and necessity
  under a Fedora driver set remain unresolved. Samsung's exact CYG1
  `of_reserved_mem.c` replaces `rbin`'s 400 MiB `size` with its 500 MiB
  `expand_size` above 8 GiB under `CONFIG_RBIN`; captured `RbinTotal` and the
  500 MiB high-memory interval confirm that effective size on X810. More
  significantly, captured `CmaTotal` is 496 MiB, exactly the sum of the other
  fourteen active dynamic-pool requests. That reconciles the aggregate dynamic
  accounting (496 MiB CMA + 500 MiB rbin) while individual CMA pool placements
  are still unresolved. Mainline Linux 7.2 lacks the Samsung rbin expansion,
  so that pool cannot be transplanted naively.
- Read `/proc/memsize/reserved`, `/proc/meminfo`, and `/proc/iomem` from the
  already-running, USB-connected TWRP recovery (no reboot or write). The
  Samsung report independently names all fifteen active dynamic pools and
  reports their effective sizes/flags, confirming 496 MiB reusable CMA plus
  500 MiB reusable rbin in this recovery boot. `%pK` physical start/end fields
  are zero because `kptr_restrict=2`, so this adds pool identity and size but
  not per-pool physical placement. The read-only outputs and SHA256 hashes are
  stored in ignored `probes/android-baseline/recovery-readonly/`.
- Revisited the candidate omission against actual Linux prior art. The current
  X710 Fedora DTS has **no dynamic reserved pools** and only one
  `memory-region` consumer (Samsung sec-log); its mainline config uses generic
  32 MiB CMA. Its README reports an end-to-end hardware-verified Fedora boot
  with display, Wi-Fi, speakers/DMIC and GPU. The X810-shaped research DT is
  based on that mainline DTS and likewise has no dynamic requests/references.
  This is strong evidence that Fedora need not reproduce Android-only QSEE,
  camera/DSP/display DMA heaps or `rbin` wholesale. It is sibling-device
  corroboration, not X810 proof. The earlier broad-memory-equivalence concern
  is therefore superseded: audit each pool consumer against the selected
  mainline drivers, but dynamic-pool omission alone is not a blanket no-boot
  reason. Exact X810 static protected reservations and post-ABL root RAM remain
  hard gates.
- Experimented only in ignored work files with a no-fragment board-04 DTBO
  overlay carrying stock root board identity and a three-entry Android DTBO
  table. `fdtoverlay` accepts the no-op overlay against the mainline-derived
  tree; applying the actual Samsung board-04 overlay fails `FDT_ERR_NOTFOUND`
  because the candidate lacks expected nodes. This demonstrates a possible
  selector experiment, not that ABL selects it or that its metadata is
  accepted. No tablet writes, flashes, or reboot were done.

## 2026-09-24 — X810 ANA38407 panel compatibility audit

- Compared the captured live X810 panel tree/CYG1 Samsung source against the
  Linux 7.2 X710 ANA38407 driver. X810 is revision E (`lcd_id=0x800005`),
  while the mainline driver hardcodes revision D (`80:00:04`) and logs that an
  ID mismatch can leave the display dark until DSI reinitialization.
- The panel controller family and part of the command sequence are shared,
  but the configurations are not drop-in compatible: X810's active stock mode
  is 2800×1752 with 30/60/120 Hz variants, DSC 1.1/8bpp and two 1400×73
  slices. The X710 driver programs 2560×1600 with two 1280×100 slices and
  X710 board timings/rails. The X810 Samsung `.dat` uses D-to-Z branches for
  the shared TSP-sync sequence, which supports adaptation rather than reuse.
- A follow-up pin/regulator comparison found the X810 stock reset/TE wiring
  at TLMM GPIOs 125/86 and the `panel_ldo_en` 1.8 V enable at TLMM GPIO 187;
  these match the X710 mainline DTS wiring. This improves confidence in
  reusing the board glue. The X810 source/runtime FDT also resolves the
  mainline driver's `vddio`/`vdd`/`vci` rails to L12B/L11B/L13B at 1.8/1.2/3.0 V
  and declares 5.5 V `avdd`; its physical switch backend and active supply
  ordering remain unresolved.
- Updated `docs/HARDWARE-X810.md` with measured panel requirements. Remaining
  work: fully resolve exact regulator/PHY/mode parameters, port its DSC
  config and revision-E behavior, then validate first-light/cold-boot/resume
  on device. No panel or boot-chain test was performed.

## 2026-09-24 — Host kernel image baseline build

- Merged the active X710 repository's `config-mainline.aarch64` and
  `config-gts9wifi.fragment` into the prepared Linux 7.2.0 tree, ran
  `olddefconfig`, then built `Image` successfully with the X710 patch set on
  this host. The resulting generic/X710 Image is 67,205,632 bytes (gzip -9:
  23,483,402 bytes; SHA256
  `b3bfe64f612fb545347ad4a7e3b8da878064ceebb76d26c88870761429011666`).
  This confirms a host can compile the existing port's kernel image and
  hardware-independent build inputs. It is not an X810 kernel artifact and
  was not packaged, signed, flashed, or booted.
- The combined `Image modules` target began building a very large set of
  unrelated arm64 modules; that module sub-build was interrupted rather than
  spend time on modules unrelated to the memory/ABL gate. Only `Image` is
  confirmed complete. The ignored source/build tree remains under
  `work/kernel-source/`.

## 2026-09-24 — X810 smoke boot and sibling-port comparison

- Repeated the smoke test with `rdinit=/bin/sh`, then diagnostic `/init`, using
  both the X910-style init_boot layout and X710 Fedora's vendor_boot fragment
  layout. The latest run used the checksum-pinned released X710 7.2.0 kernel
  payload with the X810 board-04 DTB. ABL decompresses the kernel, selects
  board 04 and exits UEFI normally. TWRP's `/proc/last_kmsg` now contains Linux
  traces for the exact `rdinit=/init` command line. The dedicated
  `/cache/x810-linux-probe/boot.log` now proves the initramfs `/init` ran on
  the X810 board, mounted cache (`/dev/sda28`), and persisted printk. It records
  Linux 7.2.0 and the USB ACM UDC bind. Host enumeration confirms the custom
  ACM gadget (VID:PID `18d1:d001`, product `X810 Linux smoke console`). The
  screen remains on Samsung splash by design (display subsystem disabled).
  The previous inference that Linux produced no printk was wrong: the SEC log
  is mixed with older Android/recovery records, and timestamps interleave. Do
  not interpret unrelated Android `init`/`reboot,shell` records as Linux uptime.
- Verified the *actual installed* test kernel instead of relying on the note:
  TWRP readback of `/dev/block/by-name/boot` hashes to
  `e6f484239eb2fd3e6da64c3123f73d7496f69e2ea8d240dd01e45f682cbee571`, exactly
  matching the experimental image. Its embedded gzip payload hashes to
  `6e5af2fb0afeb9fd49de754b47376c656a27df2fea059c6a68d966dc0dbb155d` and
  reports `Linux version 7.2.0-gts9wifi`—the released Fedora X710 mainline
  kernel, not the tablet's previous KernelSU kernel. The X910 Ubuntu project
  uses a distinct 7.2-rc3 base plus an X910-focused patch set. Keep the
  already-tested X710 7.2.0 as the controlled X810 baseline; switching to
  X910's older, differently patched build would confound boot-path diagnosis
  and is not justified by compatibility evidence yet.
- The latest ABL history shows two accepted `rdinit=/init` Linux launches and
  subsequent restart records `0x7` and `0x9`, then a Recovery-mode boot. The
  capture has no matching Linux panic trace or diagnostic-cache marker, so the
  exact failure remains unresolved. [AOSP's numeric bootstat map](https://android.googlesource.com/platform/system/core/+/4118221c66786eaeb1cb7bc6508f3c0ae8027d7c/bootstat/bootstat.cpp#130)
  labels 7 as `kernel_panic` and 9 as `hw_reset`; Samsung's DDI field
  equivalence is not verified, so treat this only as a lead, not a diagnosis.
- X910's physically validated Android-v4 layout puts kernel in `boot`, a
  legacy-LZ4 initramfs in `init_boot`, and DTB/cmdline/bootconfig in
  `vendor_boot`; its no-op/invalid DTBO path avoids Samsung UFDT overlay
  failure. X710 Fedora uses a different physically tested split: empty
  `init_boot`, full legacy-LZ4 initramfs in the `vendor_boot` platform
  fragment. These placements should not be mixed speculatively.
- X910 notes document misleading “stuck” symptoms as well as real early
  faults: the bootloader framebuffer remained visible after userspace had
  completed, and its USB gadget failed independently in DWC3. An earlier
  TrustZone NoC fatal occurred during TLMM probing; our X810 DTS already has
  the X910-validated `gpio-reserved-ranges = <36 4>` fix. X910 also found
  gzip initramfs was rejected; legacy-LZ4 fixed it. Our smoke ramdisk is
  legacy-LZ4.
- Research references: X910 [boot strategy](https://github.com/agcarbajo/postmarketos-galaxy-tab-s9-ultra/blob/master/docs/boot-strategy.md),
  [development notes](https://github.com/agcarbajo/postmarketos-galaxy-tab-s9-ultra/blob/master/docs/development-notes.md),
  and X710 [Fedora boot bundle](https://github.com/nacht20-de/gts9wifi-fedora-linux/blob/main/boot/build-bundle.sh).
- The bootloader splash remaining visible is expected for this diagnostic
  image: the smoke DTB deliberately disables the display subsystem, and the
  mainline `tty0` console reports a dummy device. It is not a valid indicator
  that the kernel failed. The X910 notes likewise warn that ABL can append
  `console=null` and recommend persistent logs/TWRP over an early screen.
- A console-diagnostic iteration accidentally used the stale locally built
  `Image.gz` (`#1 SMP`), not the released X710 binary (`#0.1.fc44.gts9wifi`);
  do not use that run to judge kernel behavior. Recovered the exact release
  gzip payload from the previously verified `out-next/boot.img` (SHA-256
  `6e5af2fb0afeb9fd49de754b47376c656a27df2fea059c6a68d966dc0dbb155d`) and
  built/flashed `out-release-console`. The new initramfs directly execs an
  interactive shell on `/dev/ttyGS0`. Host USB ACM terminal now works: I
  verified `uname -a`, `/proc/uptime`, `/proc/cmdline`, process list, mounts,
  memory, sysfs, and `dmesg` from the actual tablet. Mainline 7.2.0 remained
  live for >190 seconds without reboot; PID 1 is the interactive shell, the
  initramfs root is active, and `/dev/sda28` (`cache`) is mounted read/write.
- Physical-board probes from that Linux shell registered `FTS1BA90A
  Touchscreen` at I²C 10-0049, `Wacom WEZ01 S Pen` at I²C 9-0056, PMIC power
  and resin keys, plus `gpio-keys`; SM5714 charger/fuel-gauge/MUIC probe
  completed and the battery reports charging (76%, 4.143 V, 1.176 A,
  temperature 32.3 °C at capture). All eight CPU cores are online; meminfo
  reports about 10.6 GiB. DRM has no device and PCI has no devices because the
  display subsystem and PCIe node are deliberately disabled in this smoke DTB.
  This is kernel/initramfs bring-up evidence—not yet a Fedora root boot or
  touchscreen/pen interaction test.
- Remaining logged issues include missing `qcom/sm8550/adsp.mdt` and
  `regulatory.db`, the display clock RCG update warning, and deferred probes
  for audio, cpufreq, display bridge, Type-C role switch, and other suppliers.
  Writes for this probe remain restricted to `boot`, `init_boot`,
  `vendor_boot`, and `dtbo`.

## 2026-09-24 — Console continuity and Fedora rootfs readiness

- Reconnected to the USB ACM shell and confirmed it is still the X810 smoke
  Linux, not TWRP: `uname -r` is `7.2.0-gts9wifi`, `/proc/cmdline` has
  `rdinit=/init`, and uptime was 1,106 seconds when sampled. Host USB reports
  `18d1:d001`, matching the custom smoke-console gadget; no X810 ADB device is
  enumerated. The user reported TWRP, so this observation flags a mismatch
  between the reported screen/state and the connected USB endpoint; no reboot
  was issued to resolve it.
- Re-ran the read-only GPT split planner against saved X810 first/last-LUN
  captures. Both primary and backup GPT CRCs and arrays validate; all 40
  entries are parsed; entries 35–40 are still empty. A 50/50, 2 MiB-aligned
  geometry would leave `userdata` at 111.97 GiB and create `linuxroot` at
  111.98 GiB. This is a calculation only; no live GPT write or repartition
  has occurred.
- Inspected the verified Fedora 44 X710 rootfs release and its install/boot
  instructions. The tarball's `/etc/fstab` hardcodes an X710 root UUID and a
  separate ext2 `/boot` UUID, while the X810 smoke kernel currently uses an
  initramfs shell and has no filesystem-root discovery. The full upstream
  initramfs is deliberately carried in `vendor_boot` (not `init_boot`) and
  mounts root by UUID; its release kernel modules must match the 7.2.0
  `gts9wifi` kernel exactly. An X810 root boot therefore still needs an
  adapted initramfs/cmdline and fstab, plus compatibility checks for the
  X710-only WLAN BDF/firmware and X810 display. No rootfs has been written to
  the tablet and no partition has been formatted.
- Downloaded the matching 7.2.0 boot ZIP from the same signed-off release and
  unpacked its `vendor_boot` on the host. This supplies the actual Fedora
  dracut initramfs (39,817,857-byte legacy-LZ4 platform fragment), bootconfig,
  and X710 DTB. The bundled dracut `00-parse-root.sh` explicitly accepts
  `root=PARTLABEL=...`; therefore a `root=PARTLABEL=linuxroot` candidate
  requires no guessed filesystem UUID. The exact released 7.2.0 kernel
  config lists Qualcomm UFS, SCSI disk, and ext4 in `modules.builtin`, so
  discovery/mount of an ext4 root is structurally supported without loading
  separate UFS/ext4 modules. This is host-side asset/source inspection only;
  the X810 UFS cache mount is separately confirmed by the live smoke boot.
- Extracted Samsung CYG1's logical `vendor` image read-only on the host
  (`super.img.lz4` → sparse image → logical vendor EROFS). Stock WLAN assets
  are under `firmware/qca6490/` (`amss20.bin`, `m3.bin`, `bdwlan.elf*`,
  `bdwlang.elf*`, `regdb.bin`), not the `ath11k/WCN6855/hw2.1` file layout
  requested by the Linux driver. Prior-art notes warn Samsung's WLAN amss20
  crashes ath11k; no firmware was loaded on-device. This confirms an X810
  WCN6855 firmware/BDF port remains a real blocker for Wi-Fi, not for the
  already proven UFS/console root boot path.
- Next safe step: build a host-only X810 Fedora initramfs/root configuration
  against the already verified rootfs and mainline kernel, keeping the current
  console image intact. Before any UFS/GPT change, first verify the USB state
  in actual TWRP, create/validate the exact recovery and full-GPT backup plan,
  and test the proposed GPT editor against a synthetic copy of the measured
  layout. No Download Mode entry or bootloader/firmware write is part of this
  work.
- Added `tools/adapt-x810-rootfs-fstab.py` as a host-only, dry-run-by-default
  fstab adapter. It verifies the exact X710 root and /boot UUID lines before
  proposing `PARTLABEL=linuxroot` and removal of the separate `/boot` mount;
  altered/duplicate/missing expected entries fail closed. Positive and
  negative synthetic tests pass. This does not extract, patch, or write the
  Fedora rootfs yet.
- TWRP is now confirmed on the correct USB serial (`R52W7082ELA`); recovery
  reports `androidboot.em.model=SM-X810`, CYG1 bootloader, root ADB, and only
  `/cache` mounted. Re-read the X810 user-LUN GPT from TWRP: both 1 MiB edge
  captures are byte-identical to the Android baseline; `sgdisk --verify`
  reports no problems. TWRP's GPT fdisk is v1.0.4 and requires long options
  (`--print`, not `-p`). A `--pretend` 60/40 candidate (userdata 134.37 GiB,
  `linuxroot` 89.58 GiB) passed its in-memory geometry check; a second GPT
  read confirmed no table bytes changed. The tool warns that Samsung's
  40-entry GPT array is below the GPT specification's 128-entry minimum.
- To test that writer without touching the live GPT, built a sparse synthetic
  disk image in TWRP `/cache` from the measured partition table, scaling the
  4 KiB LBAs into a 512-byte-sector fixture. TWRP's exact `sgdisk` v1.0.4
  passed `--pretend`, then performed the candidate split on the *fixture*;
  `--verify` and independent host `sfdisk --verify` both reported no errors,
  and the table remained 40 entries with `userdata` and `linuxroot` at the
  intended geometry. All temporary fixture files were removed. The actual
  sda edge captures remain byte-identical to baseline; no live GPT changed.
- Built (host-only) `work/x810-smoke/rootboot-candidate/`: keeps the exact
  already-booted release 7.2.0 kernel, board-04 X810 smoke DTB, empty
  `init_boot`, and fallback `dtbo`; replaces only `vendor_boot`'s diagnostic
  ramdisk with the matching Fedora dracut ramdisk and
  `root=PARTLABEL=linuxroot`. Unpack/repack checks show the 39,817,857-byte
  initramfs is byte-identical, DTB still reports X810 board 04, all four
  Android images fit their existing partition sizes and have hash footers.
  No candidate image has been flashed. Its early USB hook uses RNDIS at
  `172.16.42.1`, offering a recovery/debug path with the display still
  disabled; this is not yet tested on X810.
- Applied the 60/40 split to the actual X810 user-LUN GPT after preserving and
  validating both original edge captures. Entry 34 now spans LBA 3,568,128–
  38,792,191; entry 35 `linuxroot` spans 38,792,192–62,275,574. Primary and
  backup GPT headers/arrays and CRCs were read back and validated. TWRP kept
  the old in-memory partition map until a recovery reboot; after reboot,
  `/dev/block/sda35` appeared at the expected 96,187,936,768 bytes.
- Formatted the shortened `userdata` as F2FS (its stock filesystem) and
  `linuxroot` as ext4; extracted the verified Fedora 44 rootfs directly from
  the host over USB (no 2 GB staging copy), changed `/etc/fstab` to
  `PARTLABEL=linuxroot`, and saved the original fstab alongside it. The new
  ext4 filesystem passed read-only `e2fsck`; F2FS format reported success.
- The first Fedora-root attempt still reached `x810-smoke#`: the separate
  `init_boot` contained the 979,683-byte smoke `/init`, overriding dracut. I
  replaced that candidate with the matching release's empty init_boot, flashed
  only `init_boot` from TWRP, and verified the readback hash. `vendor_boot`
  was the previously verified Fedora dracut image; vbmeta, bootloader and
  recovery were not changed.
- Retried normal boot without Download Mode. Fedora 44 now boots from
  `/dev/sda35` (ext4, `PARTLABEL=linuxroot`) on the 7.2.0-gts9wifi kernel;
  uptime was 5 minutes, root is rw, and the 10 GiB RAM is enumerated. The panel
  remains dark because the intentionally conservative smoke DT disables it.
  The Fedora initramfs enumerated its CDC Ethernet gadget; after assigning the
  host's USB NIC `172.16.42.2/24`, ping to device `172.16.42.1` succeeds and
  SSH works with the upstream default `fedora` account. This confirms the
  kernel -> initramfs -> internal UFS Fedora root path end-to-end.
- Remaining known blockers are hardware bring-up, not root boot: display is
  intentionally disabled, Wi-Fi has not been validated, and the ADSP fails to
  load `qcom/sm8550/adsp_dtb.mdt` (`-22`), so two sensor/audio-related units
  fail. Video firmware also errors, which is expected with this stripped smoke
  device tree/firmware setup. The X810 board-04 DTB is confirmed live. Do not
  infer the tablet is in TWRP while USB reports gadget serial `fedora-gts9wifi`;
  at this observation it is running Fedora, not recovery.
- The subsequent ADSP errors are attributable to a concrete X710/X810 firmware
  mismatch worth testing, not merely an unexplained driver failure: CYG1's
  read-only `apnhlos` (sda17) contains 55 `adsp*` files and its
  `adsp.mdt`/`adsp_dtb.mdt` hashes differ from the Fedora release's copies.
  In TWRP, replaced the Fedora ADSP firmware set on `linuxroot` with those
  exact X810 CYG1 files; preserved all 55 former files under
  `/opt/x810-fw-audit/fedora-before-x810-adsp`. APNHLOS was mounted read-only;
  only the Linux root partition was written. This is pending a Linux boot
  test; no firmware or Download Mode partition was changed.
- Retested from TWRP by booting the existing Fedora image (no Download Mode).
  At the first measurement the remoteproc service had not yet run; after its
  configured 25 s delay, the exact X810 firmware loaded and
  `/sys/class/remoteproc/remoteproc0/state` became `running`; kernel logged
  `remote processor adsp is now up`. On the current boot this remains running
  and `systemctl --failed` is empty. The earlier boot ended with a normal
  systemd power-off after logind recorded a short power-key event, not a panic.
  The panel still cannot show anything: smoke DT explicitly disables the DPU,
  panel regulators, and PCIe. USB CDC networking/SSH remains the working
  console. This validates both the internal-root boot and a first Samsung
  subsystem using this tablet's own signed firmware.
- Tested a non-invasive visual handoff while keeping the DPU, panel rails, and
  PCIe disabled: added the measured 44 MiB splash-framebuffer carveout
  (`0xb8000000`–`0xbaafffff`, `no-map`) plus a 2800×1752
  `simple-framebuffer` node to `vendor_boot`. The first attempt omitted the
  reservation, and `simpledrm` correctly refused to claim the range because it
  was still System RAM. The corrected image bound `simpledrm`, created `/dev/fb0`
  and DRM `card0`, and reports a connected 2800×1752 output. However, captured
  framebuffer memory is essentially blank (only 48 nonzero bytes of 19.6 MB),
  so this does not yet produce visible UI; it confirms the safe memory handoff,
  not panel initialization. Linux remains responsive over USB SSH, and the
  exact X810 native DSI timings/initialization still must be ported to light
  the display.
- User has now visually confirmed full Linux kernel/console text on the panel.
  This verifies simpledrm/fbcon presentation end-to-end; the image in use is
  the preserved live `vendor_boot` base plus the 44 MiB reservation and
  simple-framebuffer handoff (SHA-256
  `0b2d93a7a39702f74b0163c9decbd86ec2919c287ef3433786e5996ad1f29cf7`).
  It is a text console, not native DPU/DSI operation or a desktop. Tested
  GNOME/GDM once: it repeatedly spawned greeters that died, leaving no live
  graphical session, so stopped GDM to avoid the retry/CPU loop. The Fedora
  system itself is still running and accessible over USB SSH; framebuffer
  console remains the known-good visible state. Next work is X810-specific
  mainline DPU/panel support, beginning with exact init/DSC/timing and rail
  sequencing; do not risk the working simplefb path while developing it.
- Continued X810 panel audit against the CYG1 `.dat` and selected live FDT:
  the X710 driver contains `SLEW_BOOSTING_OFF/ON` commands absent from X810;
  X810 instead begins with VBP + display-on-delay writes before sleep-out.
  Confirmed X810 ID `80:00:05`, reset `<0 10 1 1>`, 2800×1752 modes and
  1400×73 DSC geometry; its 8-bpp rate-control table matches the known-working
  X910/X710-family DRM config. The X910 Ultra project's mainline ANA38407
  driver confirms the safer VBP→display-delay→sleep-out ordering is viable for
  the shared DDIC, but not that X810 DPU bring-up is already solved. The
  MAX77816/AVDD uncertainty remains; keep its rail inherited/already-on rather
  than attempting the unsupported X710 GPIO11 switch. No device image changed.

## 2026-09-26 — split project repos and current installed state

- Read-only SSH confirmed the live tablet is `gts9-fedora`, Fedora 44,
  kernel `7.2.0-gts9wifi`, root `/dev/sda35` ext4 `rw` on `PARTLABEL=linuxroot`.
  No device write or reboot occurred during this repository reorganization.
- Created the separate `x810-fedroid`, `tab-companion`, and
  `linux-ports-docs` working repos. X810 Fedora source was seeded from the
  pinned X710 Fedora prior-art snapshot and augmented with existing X810
  research/tools/overlay. Large raw captures and 65 GiB of local scratch were
  deliberately not copied. The exact X810 recovery ZIP is retained locally,
  ignored by Git, and its SHA-256 is unchanged.
- Moved Tab Companion package snapshots and X810 boot-switch integration out
  of the Fedora port repo into the app repo; Ubuntu APT/dpkg updater files are
  retained there as a baseline. A unified cross-distro updater is not yet
  implemented. Shared, concise project requirements/status are now in
  `linux-ports-docs`.
- The X710 `INSTALL.md` is retained as a reference only; X810 `INSTALL.md`
  explicitly says there is no validated clean-install/release procedure yet.
  X810 hardware and memory-map checks remain device-specific gates.
