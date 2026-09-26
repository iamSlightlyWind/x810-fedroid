> **Historical snapshot:** this status predates the first Fedora boot. Fedora 44 now boots from internal `linuxroot`; see the current concise summary in `linux-ports-docs/x810-fedora.md`. The detailed gates below remain relevant, but any old “not yet attempted” statements are no longer current.

# Research status

## Scope and safety boundary

This is the original baseline inventory of the exact SM-X810, first accessed
through wireless ADB and then USB TWRP. Its initial read-only state is historical:
later dated `PORTING-LOG.md` entries record the authorized partition split,
rootfs install and tested Fedora boot. Current hardware state and any pending
write/reboot authorization must be rechecked before operating on the tablet.

## Initial measured facts

See [PORTING-LOG.md](PORTING-LOG.md) for the dated evidence and
`probes/android-baseline/` (local-only, git-ignored) for the raw capture.

| Area | Finding | Confidence / caveat |
|---|---|---|
| Identity | SM-X810 / `gts9pwifi`, arm64, SM8550/Kalama | High; live Android properties |
| Firmware | Android 15, build `X810XXS5CYG1`, fingerprint recorded in log | High; live properties |
| Bootloader | revision 5 indicated by build/bootloader suffix; orange verified-boot state and flash-locked=0 | High for observed state; TWRP command line independently names X810/CYG1 |
| Root | `su -c id` succeeds under KernelSU; SELinux enforcing | High for current session only |
| Slots | empty slot suffix; a single named boot-image set | Likely non-A/B; verify against GPT and stock images |
| Wi-Fi | PCI `17cb:1103`, subsystem `17cb:0108` | High; kernel upstream maps device ID to WCN6855/ath11k family. Firmware and calibration remain unverified |
| Display | FDT model says GTS9PWIFI board-id 04; cmdline reports `GTS9P_ANA38407_AMSA24VU05`, LCD ID `800005` | Measured handoff labels; full panel identity still requires exact downstream source/firmware correlation |
| Touch / pen | logs report `fts_touch` at `0x49`, `wacom_w90xx` at `0x56` | Controller family measured; board wiring/configuration not yet reconstructed |
| UFS | Six LUNs sda–sdf on the SM8550 UFS controller, each with verified 4 KiB GPT; sda has 34 user/data entries; `userdata` is partition 34 | High for current live tables; primary/backup header and entry-array CRCs validate. First/last MiB captures are not full backups |
| Boot images | Read-only host copies of seven live partitions; boot/init_boot/vendor_boot use Android v4 formats; dtbo is a 3-entry Android DT table | Image headers/table and AVB parsed locally; exact firmware compared; ABL selection behavior still open |
| Board DT selection | DTBO entry 04 says Samsung GTS9PWIFI board-id 04 and matches the runtime FDT board-id tuple | Strong stock overlay match, but future/mainline ABL selector behavior is not proven |
| AVB metadata | Captured `vbmeta` reports algorithm NONE, flags 2, no descriptors, rollback index 0 | `avbtool` on captured bytes; do not infer flashability; live `sde` is kernel-read-only |
| Samsung source | CYG1 source archive includes `gts9pwifi_eur_open` and r04 board overlay | Exact board/revision and panel/controller definitions now available; Android downstream source, not mainline DTS |
| Live image modifications | KernelSU markers/files in kernel and init_boot; TWRP binary in recovery; modified live vbmeta | Host-side image inspection plus owner confirmation; preserve current working set |

## Prior-art baseline

The current X710 repository snapshot is commit
`ab123e7d1dbc0cbcd35661f9761197e977b15aa`. Its current README and kernel spec
pin Fedora 44 and Linux 7.2.0; the issue register says kernels 7.2.1–7.2.6
break X710 Wi-Fi. Keep that baseline initially. The live X810 WLAN PCI ID
matches the WCN6855 family rather than X910's WCN7850, making X710 WLAN
software a better starting point, but X810 calibration/BDF and cold-start
sequencing must be established independently.

The X910 repository snapshot is current at retrieval and documents a tested
header-v4 boot chain, root filesystem on internal UFS, non-table DTBO fallback,
`vbmeta` invariant, boot-set swapping and independent TWRP recovery. These are
design patterns, not X810 facts. X910 partition sizes/indices, DT selector,
hardware, and exact image layout must not be copied.

## Exact firmware candidate

The live build resolves to Android 15 / `X810XXS5CYG1`, CSC
`X810OXM5CYG1`, sales code XSP, binary revision 5. A third-party firmware
index lists an XSP package with those matching identifiers, Android 15, and
14.2 GB package size (filename shown as
`XSP-X810XXS5CYG1-20250729163510.zip`). This is only a candidate source, not
**Obtained and verified locally:** the full Samsung firmware bundle is present
outside this repo under the user's Downloads directory. Its outer ZIP MD5 is
`e995aca49b2e17c51cdcfdc0866b297d`, matching the candidate listing. AP, BL,
CSC and HOME_CSC archives are present. AP and BL were inspected/extracted
read-only on the host; nothing was flashed. The AP contains the stock boot,
init_boot, vendor_boot, dtbo and recovery; the BL contains the actual `vbmeta`
image. Do not confuse AP's `vbmeta_system.img` with the separate vbmeta image
in BL.

The AP's `dtbo.img` and `vendor_boot.img` are byte-identical to the live device
captures, directly tying those live images to this CYG1 firmware. AP's boot,
init_boot, and recovery images differ from the captured live copies, as does
BL's stock vbmeta versus the live vbmeta. The live set is therefore partly
modified relative to this firmware; determine the provenance of those changes
before treating it as an untouched Android restore set.

`Kernel.zip` also contains a matching Samsung open-source source archive,
`SM-X810_15_Opensource.zip`. This archive includes an X810/GTS9PWIFI target
(`BUILD_TARGET=gts9pwifi_eur_open`), Samsung board overlay sources for board
revisions 00/02/04, and the exact `GTS9P_ANA38407_AMSA24VU05` panel source.
This resolves a major source-search gap. It is Android 15 downstream source,
not mainline DTS; selector and memory-map conclusions still need comparison to
the live DT and safe translation to mainline.

## Boot-chain decode (host-side)

The live partition captures passed SHA256 checks. Host tools identify `boot`
as Android boot header v4 with the ARM64 kernel and no ramdisk; `init_boot` v4
contains an LZ4 ramdisk; `vendor_boot` v4 contains a platform LZ4 ramdisk,
vendor cmdline/bootconfig and generic Kalama base DTB. The vendor base DTB has
`qcom,board-id = <0 0>`. The separate `dtbo` is a table of three overlays for
board IDs 00, 02 and 04; its board-04 overlay matches the Samsung model and
board-id observed in the running FDT. This suggests, but does not prove, stock
ABL's selection/handoff behavior for a replacement image. `vbmeta` parses as
algorithm NONE, flags 2, no descriptors, rollback 0. All decoding occurred on
the host; no device state was changed. Details and caveats are in
[`BOOT-STRATEGY.md`](BOOT-STRATEGY.md).

## Gates before any boot or write experiment

1. Finish source review: full relevant X710/X910 scripts, DTS, patches,
  release/workflow relationships, and histories.
2. Use the exact CYG1 image comparison and matching Samsung source to determine
  live boot modifications and derive ABL DTB/DTBO selection semantics before
  constructing test images.
3. Preserve a complete, read-only GPT/PIT backup for all LUNs before any
  future table change. The current mapping, GUID fields, partition geometries
  and kernel read-only flags are recorded in `PARTITION-LAYOUT.md`; current
  first/last-MiB copies are not a full backup.
4. Reconstruct X810 FDT, ABL selectors and protected memory map; no mainline
  DT is safe to boot before the carveouts are reconciled.
5. Prove host-controlled TWRP or Download Mode recovery before any destructive
  operation or first mainline boot. TWRP USB ADB is now verified, but Download
  Mode flashing and recovery behavior after an intentionally bad boot remain
  untested. Do not infer write safety from shell access.

## Open questions

- Exact PIT/build association and active ABL selection/DTB handoff behavior;
  firmware/image comparison and matching Samsung source acquisition are now
  complete at the artifact level. The `vbmeta` LUN itself is kernel-read-only
  during One UI.
- Full Samsung package MD5 matches its published CYG1 candidate checksum; the
  live-vs-stock differences and installed boot image modifications remain to
  be understood.
- Whether Download Mode flashing is host-controllable, and whether recovery
  remains reachable after a failed boot. TWRP ADB works over USB now, but this
  alone does not establish either behavior.
- Exact Samsung source archive was obtained with `gts9pwifi` board overlays
  and X810 panel code. Archive has no Git commit metadata; ABL selector
  semantics still need source/live correlation.
- A current SM8550 Android DT source candidate
  (`samsung-sm8550/android_kernel_samsung_sm8550-devicetrees`, commit
  `704fadc1...`) has no `gts9p`/`gts9pwifi` board DTS in the checked snapshot;
  it remains common-binding prior art only. Exact CYG1 Samsung source has now
  been obtained. `MEMORY-MAP.md` records an exact 50-range X810 static-map
  match in a research derivative, plus the exact eight-range runtime RAM map.
  Three runtime-only ranges remain outside the Samsung source tree. Dynamic
  reservations total 496 MiB reusable CMA plus 500 MiB rbin and are named in
  a read-only recovery memsize capture. Fedora X710 prior art omits those
  Android pools and uses 32 MiB generic CMA, so the X810 port needs a
  per-consumer audit rather than wholesale pool transplantation. CYG1 ABL
  handoff remains the critical untested memory gate.
- WLAN firmware/BDF source, radio state, cold-boot sequencing, and whether the
  matching WCN6855 prior-art workaround applies.
- Touch/pen and power/audio/display component wiring, firmware and bindings.
- Whether the current boot-chain images are rooted/modified and match the
  exact stock package.
