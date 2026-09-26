# Memory-map research — preliminary stock X810 FDT inventory

## Status / safety gate

The live X810 device tree and `/proc/iomem` were captured read-only on
2026-09-24 and are kept in local-only `probes/android-baseline/`. This page is
an initial inventory, **not a validated Linux memory map**. Address tuples
below are decoded from the live FDT's `reserved-memory/*/reg`; the FDT has
two address cells and two size cells. A region's label is not proof of its
actual owner, placement contract, or whether all firmware treats it as
exclusive. No region has been borrowed for Linux, ramoops, or framebuffer use.

No mainline image should be booted until every active/reserved range is
reconciled against `/proc/iomem`, the stock DTB/DTBO, SM8550 base definitions,
remoteproc/secure firmware ownership, and sibling prior art. A TrustZone/NoC
reset is a stop-and-rethink signal, not a retry prompt.

## High-risk ranges observed

Start and size values are taken literally from the stock live FDT. `no-map`
means the node contains that property in the captured tree. Semantic owner
labels are provisional; confidence is high that the FDT contained these bytes,
low-to-medium for ownership interpretation pending exact source.

| Start | Size | Node / label | `no-map` | Owner hypothesis / caveat |
|---:|---:|---|:---:|---|
| `0x80000000` | `0x00a00000` | `gunyah_hyp_region` | yes | hypervisor-protected; never reclaim |
| `0x80a00000` | `0x00400000` | `cpusys_vm_region` | yes | CPU-system VM/firmware |
| `0x81a00000` | `0x00260000` | `xbl_dt_log_merged` | yes | bootloader log/config handoff |
| `0x81c60000` | `0x00020000` | `aop_cmd_db_region` | yes | AOP command database |
| `0x81c80000` | `0x00074000` | `aop_config_merged_region` | yes | AOP configuration |
| `0x81d00000` | `0x00200000` | `smem_region` | yes | shared memory |
| `0x81f00000` | `0x00020000` | `adsp_mhi_region` | yes | ADSP/MHI |
| `0x82700000` | `0x00100000` | `tz_stat_region` | yes | TrustZone status |
| `0x8a800000` | `0x10800000` | `mpss_region` | yes | modem/MPSS despite Wi-Fi product; firmware-owned |
| `0x9b000000` | `0x00080000` | `q6_mpss_dtb_region` | yes | remoteproc DTB |
| `0x9b080000` | `0x00010000` | `ipa_fw_region` | yes | IPA firmware |
| `0x9b090000` | `0x0000a000` | `ipa_gsi_region` | yes | IPA GSI |
| `0x9b09a000` | `0x00002000` | `gpu_micro_code_region` | yes | GPU microcode |
| `0x9b100000` | `0x00180000` | `spss_region_region` | yes | secure processor shared area |
| `0x9b280000` | `0x00060000` | `spu_tz_shared_mem` | yes | secure processor / TrustZone |
| `0x9b2e0000` | `0x00020000` | `spu_modem_shared_mem` | yes | secure processor/modem share |
| `0x9b300000` | `0x00800000` | `camera_region` | yes | camera/firmware heap |
| `0x9bb00000` | `0x00700000` | `video_region` | yes | video/VPU |
| `0x9c200000` | `0x00700000` | `cvp_region` | yes | CVP |
| `0x9c900000` | `0x02000000` | `cdsp_region` | yes | CDSP |
| `0x9e900000` | `0x00080000` | `q6_cdsp_dtb_region` | yes | remoteproc DTB |
| `0x9e980000` | `0x00080000` | `q6_adsp_dtb_region` | yes | remoteproc DTB |
| `0x9ea00000` | `0x059b4000` | `adspslpi_region` | yes | ADSP/SLPI shared pool |
| `0xb8000000` | `0x02b00000` | `splash_region` | no | current boot graphics handoff; may be reclaimable only by proven contract |
| `0xd8100000` | `0x00040000` | `xbl_sc_region` | yes | bootloader secure context |
| `0xd8140000` | `0x001c0000` | `cpucp_fw_region` | yes | CPUCP firmware |
| `0xd8300000` | `0x00500000` | `qtee_region` | yes | QTEE |
| `0xd4d00000` | `0x03300000` | `mpss_dsm_region` | yes | modem DSM |
| `0xff800000` | `0x00600000` | `llcc_lpi_region` | yes | LLCC low-power state |
| `0x880100000` | `0x000ff000` | `sec_debug_region_pool` | no | Samsung crash/debug pool |
| `0x880200000` | `0x00200000` | `sec_log_buf_region` | no | Samsung kernel log |
| `0x880400000` | `0x00500000` | `sec_debug_bl_region` | yes | Samsung bootloader debug |
| `0x880900000` | `0x00200000` | `sec_pmsg_region` | no | Samsung persistent messages |
| `0x880b00000` | `0x00001000` | `google_debug_kinfo_region` | yes | debug metadata |
| `0x880b01000` | `0x00001000` | `hdm_region` | yes | device/security metadata |
| `0x880c00000` | `0x0ad00000` | `sec_qcom_rdx_bootdev_region` | no | Samsung/QTI diagnostic range; semantic ownership unresolved |
| `0xf3800000` | `0x040ee000` | `trust_ui_vm_region` | yes | TrustUI VM |
| `0xf78ee000` | `0x00001000` | `trust_ui_vm_dump` | yes | TrustUI dump |
| `0xf78ef000` | `0x00009000` | `trust_ui_vm_qrtr` | yes | TrustUI transport |
| `0xf78f8000` | `0x00004000` | `trust_ui_vm_vblk0_ring` | yes | TrustUI transport |
| `0xf78fc000` | `0x00004000` | `trust_ui_vm_vblk1_ring` | yes | TrustUI transport |
| `0xf7900000` | `0x00100000` | `trust_ui_vm_swiotlb` | yes | TrustUI DMA bounce area |

The table is intentionally only a high-risk subset. The tree also has
zero-sized/placeholder nodes (for example `kaslr_region`, `ramoops_region`,
`audio_cma_region`, `cnss_wlan_region`, and shared heaps without literal
`reg` ranges), which need examination with their `size`, `alignment`,
`alloc-ranges`, `status`, and consumer references. FDT node names sometimes do
not match `reg` addresses. Do not silently assume names are canonical.

## Exact CYG1 Samsung-source cross-check (2026-09-24)

The exact `SM-X810_15_Opensource.zip` supplied with this CYG1 package contains
the downstream Qualcomm `kalama.dtsi` reservation base and the exact
`gts9pwifi_eur_open_w00_r04.dts` board overlay. A host-side comparison of the
runtime FDT, source base, and r04 overlay found that 49 populated static
reservation tuples agree by node/range after applying board overlay overrides.
For example, overlay fragment 103 changes `adspslpi_region` to
`0x9ea00000 + 0x059b4000`, matching the live FDT (the base DTS value alone is
smaller and must not be used without its board overlay).

Three additional concrete `reserved-memory` ranges appear in the live runtime
FDT but have no matching named node in the exact Samsung source archive:

| Runtime-only region | Range | `/proc/iomem` observation | Treatment |
|---|---:|---|---|
| `kaslr_region` | `0xb01ff000 + 0x1000` | Reserved | Unknown provenance; do not reclaim |
| `uh_heap_region` | `0xb0200000 + 0x40000` | Reserved | Likely related to the hypervisor; owner not proven; do not reclaim |
| `uh_guest_region` | `0xb1000000 + 0x3600000` | Reserved | Likely related to the hypervisor; owner not proven; do not reclaim |

### Sibling-port correlation (2026-09-24)

The current X710 Fedora DTS (upstream clone commit
`ab123e7d1dbc0cbcd35661f9761197e977b15aa`) and X910 Ubuntu DTS (clone commit
`4ff9d4b0ba1ae40e7605ad54c0ffe561c1e26a60`) independently contain the same
`kaslr`, `uh_heap`, and `uh_guest` base addresses, and both mark those three
nodes `no-map`. This is strong corroboration that all three are intentional
firmware-owned exclusions, but it does not establish X810-specific ownership
semantics. The exact X810 live FDT marks `kaslr_region` `no-map`; its
`uh_heap_region` and `uh_guest_region` have static `reg` values but no
`no-map`, `reusable`, or other allocation property. Both still appear as
reserved in the X810 live `/proc/iomem`.

The `uh_guest` size is device-specific across the family: X710 DTS uses
`0x03000000` (48 MiB), the X810 live FDT uses `0x03600000` (54 MiB), and X910
DTS uses `0x03a00000` (58 MiB). Do not substitute a sibling size. The X810
runtime tuple remains the authoritative value and must be retained exactly.
The `uh_` prefix suggests a hypervisor-related purpose, but the exact Samsung
source does not define these nodes and the name alone is not a verified owner
assignment. For any future X810 mainline DT, preserve all three exact ranges;
whether to apply `no-map` to the two X810 nodes that lack it remains a DT
semantics decision to validate against upstream binding/kernel behavior and the
actual X810 firmware contract before boot.

The sibling-port history provides a concrete cautionary failure: the X910
postmarketOS port observed a fatal TrustZone/NoC reset during an early Linux
boot after its DTB omitted several live stock carveouts, then added those exact
measured ranges to its DTS. That demonstrates why omitted reservations can be
fatal on this family; it does not prove that those omissions were the sole
cause or that every X810 behavior matches X910. Treat this as prior-art risk
evidence only, not a substitute for the X810 complete map and validation.

### Full fixed-range comparison to the X710 port

Compiled the X710 port DTS against the signed Linux 7.2.0 source and compared
its flattened `/reserved-memory` tree to the measured X810 CYG1 runtime FDT
with `tools/compare-reserved-memory.py`. The X810 runtime tree has 50 nonzero
fixed ranges; the X710 port DTB has 62. Seven X810 ranges are absent by exact
address/size in X710: the X810 `uh_guest_region` (the smaller X710 node starts
at the same address but ends 6 MiB early) and the X810 TrustUI range plus its
six associated transport/bounce subranges at `0xf3800000..0xf79fffff`.

The X710 DTS also has 19 fixed ranges absent from X810's runtime tree. Several
overlap physical ranges that X810 `/proc/iomem` explicitly calls `System RAM`,
including `hyp-tags-reserved-region` at `0x811d0000`, `cdsp-secure-heap-region`
at `0x82800000`, and `ta-region` at `0xd8800000`. Thus blindly booting the X710
DTS on X810 would both omit measured X810 exclusions and impose sibling-only
reservations over X810 RAM. This is a concrete rejection of “copy X710 DTS,
change model string” as a safe porting method. It is an offline audit of DTs,
not a device boot test.

The comparison tool checks exact range and `no-map` parity, exact root RAM
range parity, candidate reservation self-overlap, and candidate-only ranges
overlapping measured RAM. It is intentionally stricter than a DTS validator:
any difference must be reconciled explicitly before a candidate is accepted.

Do not infer who inserted these regions solely from their names; the source
overlay does not define them. This exact-source agreement materially improves
the static X810 map, but is not permission to boot: every dynamic pool,
high-memory reservation, ownership transfer, FDT handoff and mainline kernel
reservation still needs review. Preserve all three runtime-only areas.

## Required next steps

1. Decode every FDT node with parent cell-size semantics and active status;
   flag zero-sized, dynamic allocation and overlapping ranges.
2. Compare the result with captured `/proc/iomem`, including high physical
   addresses and protected memory.
3. Find the exact X810 downstream DT source/build and stock overlays; establish
   whether the live FDT is a composed base-plus-overlay result.
4. Compare against SM8550 common definitions and X710/X910 maps. A sibling
   region can only be used as corroboration, never as X810 ownership proof.
5. Create the plan's machine-checkable memory-map validator and tests only
   after the map and reservation semantics are independently established.

### Offline X810-shaped map derivative and current memory gate (2026-09-24)

For research only, an ignored copy of the X710 DTS was modified to remove
sibling-only fixed reservations, restore the X810 54 MiB UH guest range, add
X810's measured TrustUI ranges, and match live `no-map` flags. This reduced
all 50 reservation tuples to an exact address/size/attribute match. During
this check, the audit found four sibling-DTS attribute mismatches that had
not been caught by the earlier one-way `no-map` check: X710 marks its splash,
secure debug pool, pmsg buffer, and Qualcomm reset-diagnostics region
`no-map`, while the measured X810 runtime FDT does not. The tool now requires
exact `no-map` parity and checks for candidate reservation overlaps.

After that earlier audit, the work-only candidate was given the eight measured
X810 root RAM tuples. `tools/compare-reserved-memory.py` now reports an exact
static range/attribute and root-RAM match against the runtime FDT. That is a
pre-ABL structure comparison only: no test proves CYG1 ABL preserves these
tuples or accepts this derivative, and the tree still contains sibling X710
peripheral content. It is explicitly not boot-ready.

#### Dynamic reserved-memory requests: newly identified blocker

The same live runtime FDT contains **19 dynamic `/reserved-memory` nodes**
with `size` and no fixed `reg`, catalogued by
`tools/list-dynamic-reserved.py`. Fifteen are active and request 896 MiB in
base sizes (996 MiB after CYG1's X810 `rbin` expansion); four are explicitly
disabled (8 MiB SDSP, 28 MiB audio CMA, 4 KiB
debug-kinfo and 2 MiB ramoops). Every active pool is missing from the current
X810-shaped candidate, which requests zero dynamic reserved memory. This is a
measured difference, but it is not by itself proof of an unsafe Linux map.

Active requests include default CMA (32 MiB), display (164 MiB), WLAN (32
MiB), DSP/CDSP pools, QSEE/QSEEcom (including reusable 400 MiB `rbin`), QMC,
user-contiguous memory, and diagnostic dump memory. Several have active DT
consumers: `cnss_pci0` references the WLAN pool; Qualcomm CVP's
`mem_cdsp` references `cdsp_eva_region`; Qualcomm DMA-heap entries reference
display, qseecom, secure CDSP, demura, SP HLOS and user-contig pools; QSEEcom,
QMC memshare, VA minidump, audio DSP and `mem_dump` nodes reference their
respective regions. Some consumers are Android/QTI-specific and may be
disabled or absent in a Fedora port; that does **not** justify removing their
memory descriptions before the driver/firmware handoff and the exact placement
are understood. `rbin` is especially non-generic: its FDT `size` is 400 MiB
and `expand_size` is 500 MiB. Exact CYG1 Samsung code replaces the size with
the 500 MiB value on devices above 8 GiB under `CONFIG_RBIN` (not 400 MiB plus
500 MiB); captured `/proc/meminfo` reports `RbinTotal: 512000 kB`, confirming
500 MiB on this X810. Linux 7.2 has no corresponding implementation. Do not
copy that node blindly, and do not assume omission is harmless.

The live `/proc/meminfo` gives `CmaTotal: 507904 kB` (496 MiB) and
`RbinTotal: 512000 kB` (500 MiB). These exactly reconcile with the dynamic
DT requests: the fourteen active non-rbin pools sum to 496 MiB, and X810's
expanded rbin is 500 MiB. This strongly corroborates the total dynamic-memory
budget, not every pool's physical placement. In `/proc/iomem`, the high range
`0x9e0000000-0x9ff3fffff` is exactly 500 MiB and agrees with `RbinTotal`; the
other variable/reserved intervals do not yet identify each pool's address.
On 2026-09-24, a read-only query of the attached TWRP recovery exposed
`/proc/memsize/reserved`. It reports the named dynamic pool sizes and `map`,
`nomap`, `reusable` classes, but its `%pK` base/end addresses are zero because
`kptr_restrict=2`; the TWRP `/proc/iomem` still supplies anonymous physical
reserved intervals. Raw recovery snapshots and their hashes are in ignored
`probes/android-baseline/recovery-readonly/`. This is not a fresh Android
runtime capture, but confirms the stock X810 kernel recognizes all fifteen
active pool names and sizes. `tools/check-dynamic-reserved-memsize.py`
compares those pool names, effective sizes and flags against the runtime FDT,
then validates `CmaTotal`/`RbinTotal`; it passes for these captures.

#### What this means for Fedora, and what remains a gate

The Fedora X710 prior-art DTS contains no dynamic Qualcomm Android heaps and
only one `memory-region` consumer (the Samsung persistent log). Its config
uses 32 MiB generic CMA, and its repository reports verified hardware boots
with mainline display, Wi-Fi, speakers/DMIC, and GPU. This is strong evidence
that reproducing the entire Android 496 MiB CMA heap set plus Samsung's 500
MiB `rbin` is **not inherently required** for a mainline Fedora port. Those
Android pools are not automatically Linux firmware carveouts: most are
reusable DMA heaps for QSEE, camera/display, audio DSP, CVP, QMC, VA and
diagnostics; `rbin` is a Samsung-specific Android cache heap. X710 is
corroboration, not a guarantee for X810.

For the initial X810 Fedora DT, omission is plausible if the selected mainline
drivers and DT contain no consumers of those pools, generic CMA is enabled,
and every actual hardware/firmware-owned static carveout remains protected.
The current X810-shaped candidate has no dynamic pool requests, and all six
`memory-region` properties in its compiled mainline DTS reference fixed
reservations; no reference targets a removed dynamic node. This establishes
structural closure for the current candidate, not that every future X810
feature should omit every pool.

| Android dynamic pool(s) | Exact stock consumer evidence | Initial Fedora treatment |
|---|---|---|
| `linux,cma` (32 MiB) | `linux,cma-default` | Keep generic CMA enabled (32 MiB X710 config baseline). |
| `cnss_wlan_region` (32 MiB) | `pcie0_rp/cnss_pci0` | Mainline ath11k/WCN6855 X710 baseline works without this CNSS pool. |
| `non_secure_display_region`, `demura_heap_region` | QTI DMA heaps `qcom,display`, `qcom,demura` | Use mainline DRM/GEM path; no Android dma-heap consumer in candidate. |
| `cdsp_eva_region`, `secure_cdsp_region`, `adsp_heap_region`, `va_md_mem_region` | CVP, secure-CDSP/demura heaps, FastRPC, VA minidump | Omit vendor services/heaps; retain the candidate's fixed remoteproc/firmware carveouts. |
| `qseecom_region`, `qseecom_ta_region`, `sp_region`, `user_contig_region` | QSEEcom and Android DMA heaps | No mainline Fedora consumer; omit proprietary TEE heap clients. |
| `qmc_dma_region` | Qualcomm memshare client labeled `modem` | No Fedora modem consumer on the Wi-Fi target. |
| `mem_dump_region`, `rbin` | Qualcomm dump diagnostics; Samsung rbin DMA heap/cache | Optional Android diagnostics/cache; omit unless a Linux feature adds a consumer. |

This treatment is specific to the currently researched first-boot mainline
configuration. If bringing up Android-compatible CVP/FastRPC/secure heaps,
porting a consumer that asks for one of these pools, or changing the target
device-tree, revisit the relevant row and allocation contract. The candidate
still requires a controlled first boot to validate generic CMA demand and
runtime firmware behavior after the independent ABL/static-map gates pass.
Do not transplant vendor pools, especially `rbin`, as a reflex: mainline does
not implement its Samsung expansion/cache behavior. Conversely, if an X810
driver or firmware interface references a pool, retain an appropriate
mainline-compatible allocation or disable that feature before boot.

Thus dynamic-pool placement is no longer a blanket reason to block a
mainline-Fedora candidate; the concrete remaining memory gate is exact X810
root RAM and static protected-range handoff through CYG1 ABL, plus auditing
that Linux device consumers match the intentionally smaller mainline pool
set. No candidate has been passed through ABL or booted. The list tool's
`alloc-ranges` are allocation constraints, not measured addresses.
