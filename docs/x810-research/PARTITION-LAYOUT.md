# X810 partition layout — original stock GPT evidence and later split

Captured 2026-09-24 from the connected SM-X810 before its userdata split. The
original GPT was read-only captured from `/dev/block/sda` and verified from
primary/backup GPT regions (`probes/android-baseline/sda-first-1m.bin` and
`sda-last-1m.bin`, local-only). A later, separate device operation created a
60/40 Android/Fedora split; see the measured post-split geometry below.

## Geometry

- `/dev/block/sda` reported `255080792064` bytes (62,275,584 GPT LBAs of 4096
  bytes each; Android `/proc/partitions` reports 1024-byte units).
- GPT primary header: LBA 1; backup header: LBA 62,275,583; usable range
  6–62,275,574; 40 entries × 128 bytes; disk GUID
  `98101b32-bbe2-4bf2-a06e-2bb33d000c20`.
- The two GPT header copies both report entry-array CRC `0x55bea366` and the
  same disk GUID/usable range. X810 uses 4096-byte GPT logical sectors on this
  LUN; do not assume 512-byte LBA arithmetic for future tooling.
- The relevant Android dynamic `super` layout is readable with `lpdump` and
  recorded in `probes/android-baseline/gpt-and-super.txt`; no Fedora space is
  to be taken from `super`.

## User-data LUN table

First/last values below are inclusive 4096-byte GPT LBAs. GUIDs and exact raw
entries are preserved in the host-only GPT capture; partition type GUIDs are
not replaced by guessed generic Linux types.

| # | Label | First LBA | Last LBA | Size bytes | Notes |
|---:|---|---:|---:|---:|---|
| 1 | modemst1 | 265 | 1,032 | 3,145,728 | protected radio state |
| 2 | modemst2 | 1,033 | 1,800 | 3,145,728 | protected radio state |
| 3 | fsc | 1,801 | 1,801 | 4,096 | protected provisioning |
| 4 | ssd | 1,802 | 1,803 | 8,192 | protected provisioning |
| 5 | persist | 1,804 | 9,995 | 33,554,432 | calibration/state; protect |
| 6 | efs | 9,996 | 15,115 | 20,971,520 | protected identity/calibration |
| 7 | param | 15,116 | 17,675 | 10,485,760 | protected boot parameters |
| 8 | debug | 17,676 | 20,235 | 10,485,760 | keep untouched |
| 9 | sec_efs | 20,236 | 25,355 | 20,971,520 | protected identity data |
| 10 | misc | 25,356 | 25,611 | 1,048,576 | boot control/recovery state |
| 11 | frp | 25,612 | 25,739 | 524,288 | protected |
| 12 | rawdump | 25,740 | 29,835 | 16,777,216 | keep untouched |
| 13 | bota | 29,836 | 40,175 | 42,352,640 | keep untouched |
| 14 | persistent | 40,176 | 40,303 | 524,288 | keep untouched |
| 15 | steady | 40,304 | 41,327 | 4,194,304 | keep untouched |
| 16 | dsp | 41,328 | 57,711 | 67,108,864 | firmware; keep untouched |
| 17 | apnhlos | 57,712 | 96,111 | 157,286,400 | firmware; keep untouched |
| 18 | vbmeta_system | 96,112 | 96,127 | 65,536 | keep untouched |
| 19 | metadata | 96,128 | 104,319 | 33,554,432 | keep untouched |
| 20 | modem | 104,320 | 152,447 | 197,132,288 | modem firmware; keep untouched |
| 21 | boot | 152,448 | 177,023 | 100,663,296 | boot set candidate; backup first |
| 22 | init_boot | 177,024 | 179,071 | 8,388,608 | boot set candidate; backup first |
| 23 | recovery | 179,072 | 205,823 | 109,576,192 | independent recovery; preserve |
| 24 | vendor_boot | 205,824 | 230,399 | 100,663,296 | boot set candidate; backup first |
| 25 | super | 230,400 | 3,090,431 | 11,714,691,072 | dynamic Android; do not use |
| 26 | prism | 3,090,432 | 3,372,031 | 1,153,433,600 | keep untouched |
| 27 | optics | 3,372,032 | 3,379,711 | 31,457,280 | keep untouched |
| 28 | cache | 3,379,712 | 3,533,311 | 629,145,600 | keep untouched |
| 29 | omr | 3,533,312 | 3,546,111 | 52,428,800 | keep untouched |
| 30 | dtbo | 3,546,112 | 3,550,207 | 16,777,216 | boot set candidate; backup first |
| 31 | testparti | 3,550,208 | 3,551,231 | 4,194,304 | keep untouched |
| 32 | core_nhlos_a | 3,551,232 | 3,567,615 | 67,108,864 | firmware; keep untouched |
| 33 | catefv | 3,567,616 | 3,568,127 | 2,097,152 | provisioning; keep untouched |
| 34 | userdata | 3,568,128 | 62,275,574 | 240,465,702,912 | original stock size; later split as below |

## Measured post-split X810 geometry

The tablet was subsequently split once using its measured 4 KiB-sector GPT.
The observed post-split table has entry 34 `userdata` ending at LBA 38,792,191
and entry 35 `linuxroot` spanning LBAs 38,792,192–62,275,574. TWRP reported
`/dev/block/sda35` at 96,187,936,768 bytes; Fedora booted from
`PARTLABEL=linuxroot`. This demonstrates that the 60/40 layout works on this
device, but does **not** validate a general-purpose repartition ZIP or replace
the need for a complete host-side backup and readback checks before future
table writes. Android userdata was reformatted after splitting.

In the original stock table, the range between `super` and `userdata` was already occupied by
`prism`, `optics`, `cache`, `omr`, `dtbo`, `testparti`, `core_nhlos_a` and
`catefv`; do not mistake it for empty Linux space. Any future split tooling
must preserve all other GPT entries and use fresh X810 identity/geometry
checks. Never reuse X910 LBA or partition-number assumptions.

## Outstanding LUN work

The read-only inventory mapped six UFS targets under
`/sys/devices/platform/soc/1d84000.ufshc/host0/target0:0:0/`, all with 4096-byte
logical/physical blocks. Read-only first/last MiB captures for each passed
primary/backup header and entry-array CRC checks with
`tools/parse-gpt-capture.py`:

| Android block | UFS target suffix | Bytes | `blockdev --getro` | Selected named contents |
|---|---:|---:|---:|---|
| sda | 0 | 255,080,792,064 | 0 | user-data table above, `super`, Android boot images, `userdata` |
| sdb | 1 | 20,971,520 | 1 | `xbl`, `xbl_config`, `apdp`, pad |
| sdc | 2 | 20,971,520 | 0 | `xbl_b`, `xbl_config_b`, pad |
| sdd | 3 | 33,554,432 | 0 | `ddr`, XBL secure logs/test mode, pad |
| sde | 4 | 671,088,640 | 1 | TZ/ABL/UEFI/security partitions, including `vbmeta` |
| sdf | 5 | 33,554,432 | 0 | `fsg`, pad |

`vbmeta` resolves to `/dev/block/sde15` (131,072 bytes); its containing UFS
block device (`sde`) reports read-only in the current Android kernel. Do not
assume a recovery or bootloader write path until separately demonstrated.
Partitions on separate LUNs often share names only in an A/B-like sense (e.g.
`xbl` vs `xbl_b`), but X810's Android boot partition table does not advertise a
slot suffix.

Interesting device-specific GPT convention: all six GPT headers expose the
same disk GUID, and the parsed per-entry unique GUID field equals its type GUID
in these images. Both header CRCs and array CRCs validate. Preserve entries
byte-for-byte and do not regenerate supposedly “normal” GUIDs in installer
code. This is a report of observed on-device metadata, not a claim that the
layout follows ordinary PC GPT conventions.

The capture contains only the first/last 1 MiB of each LUN—not a full image or
complete byte-for-byte disk backup. It is enough to parse this GPT layout, but
the host recovery vault still needs an explicitly planned read-only full GPT
backup (or equivalent before any write). `sgdisk` on Android exists but
rejected `-p`; the included parser validates captured bytes instead.
