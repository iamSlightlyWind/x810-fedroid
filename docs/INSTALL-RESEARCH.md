# Linux-on-Tab-S9 installation prior art

Research snapshot: 2026-09-26. Goal is a Linux-PC command-line/TUI installer
for the Wi-Fi SM-X810. This is a source comparison, not a claim that another
model's flashing package works on X810. Device-specific GPT sectors, boot
images, model identity checks, firmware, and recovery behavior must be
revalidated for each model.

## Relevant projects and what transfers

| Project / device | Installation model | Useful idea | Not safe to copy to X810 |
|---|---|---|---|
| [Ubuntu Galaxy Tab S9 Ultra](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra), SM-X910 | TWRP install ZIP; optional separate UFS split ZIP; Tab Companion/Android app switches the saved boot set | One matched release, explicit destructive-data warning, one split step and one OS ZIP; TWRP and Download Mode are separate recovery layers | X910 partition starts/indices, DT/boot images, generated ZIP binaries, Samsung recovery behavior |
| [Azkali Ubuntu Touch port for Tab S9](https://gitlab.com/azkali-samsung/gts9/ubports), SM-X710 | TWRP ADB-sideload install ZIP | Simple host-side `adb sideload` path | Its device is the 11-inch SM-X710; its maintainer states it is not tested for the Plus or Ultra. The project URL may require access; a 2026 copy/fork is not automatically the upstream source |
| [Whitehat Hardware Tab S9 page](https://ports.whitehathardware.com/tab-s9/), S9 family | Project announcements describe an active family-wide Ubuntu Touch/Halium effort and later mainline work | Build locally from exact Samsung firmware, stamp packages by model/revision, back up device-unique partitions, explain recovery/rollback | As of this research snapshot, the page is not providing a tested X810 Fedora release. Treat status as moving; no external image/package has been validated on this port |
| [X710 Fedora port](https://github.com/nacht20-de/gts9wifi-fedora-linux), SM-X710 | Separate Fedora rootfs builder and boot/TWRP package; current internal-storage helper reformats all of `userdata` | CI can build and match the rootfs, kernel RPM, initramfs, boot set and recovery ZIP | Model, GPT layout, userdata behavior, boot chain, and the legacy full-userdata install script |
| [Tab S8+ mainline Linux](https://github.com/aaronsb/sm-x800-linux), SM-X800 | Mainline/postmarketOS development for a distinct device generation | Keep upstream-kernel/device bring-up and desktop installer concerns separate | SM-X800 / SM8450 partition and kernel assumptions |
| [Ubuntu Touch supported-device installer](https://devices.ubuntu-touch.io/installer/) | Friendly host GUI for *listed and supported* devices | A guided PC app can check the phone, download the right image, and sequence operations | It does not list/validate this Fedora port or make a custom Tab S9 installer appear automatically |

The Azkali Tab S9 port's public instructions describe TWRP plus an unlocked
bootloader, Android 15 or older, then one ZIP sent with `adb sideload`; the
forum says the build is for SM-X710 only. Its source is also useful for
cross-checking Samsung downstream pogo-keyboard and GPIO/power sequencing,
but it is not evidence that its userspace installer or image boots on X810.
The Ultra README likewise offers one
installation ZIP, with a preceding model-specific split ZIP for dual boot, but
warns that Android `userdata` is erased in either install mode. Those are
valuable UX patterns; they do not remove the X810 validation work.

Whitehat's page is a useful *process* reference, but its status must not be
overstated. The page's current table calls X910 stable, X710 and X810
"In bringup", lists bootloader rev 1 and rev 5, and labels the public build
"Pre-release · public builds coming" (checked 2026-09-26). It describes a
Linux-host build from the exact Samsung firmware package for the model and
bootloader revision, revision-aware release lanes, explicit TWRP/Download
Mode steps, and backups of device-unique identity/calibration partitions.
Those are valuable release/checklist patterns. They do **not** amount to an
available, tested X810 Fedora installer, and the page's unlockability/fuse
claims are community guidance, not independently verified X810 facts for this
port. Do not apply its revision or flash assumptions without checking the
tablet's actual Download Mode status and applicable current source. Its
page-listed backup partitions are useful candidates for our protected-data
inventory, not a blanket instruction to flash/restore them.

## X810 design decisions

1. Default target is **Fedora beside Android on internal UFS**, not Fedora
   inside Termux/Proot and not an SD-only rescue path. An optional destructive
   replace-Android-data mode may be documented later, but it must never be
   mistaken for dual boot.
2. The release must be model-stamped and matched: rootfs, X810 kernel/boot
   bundle, firmware version and install package come from the same validated
   release. The PC-side tool verifies hashes before asking the user to enter
   TWRP.
3. The developer has already repartitioned the physical X810 once and booted
   Fedora from the resulting `linuxroot`; exact post-split geometry is recorded
   in [`x810-research/PARTITION-LAYOUT.md`](x810-research/PARTITION-LAYOUT.md).
   The host wizard now has a measured-layout split writer that captures GPT
   metadata to the PC, verifies the stock X810 disk/table identity, changes
   only entries 34 and 35, and validates GPT readback. It has host-side mock
   tests but is **not live-validated**; it is not yet an end-user install step.
   Android-data reinitialization, rootfs installation, boot installation, and
   recovery still need an end-to-end validated workflow.
4. The boot bundle now uses the exact root and panel arguments observed on the
   currently working Fedora X810: `root=PARTLABEL=linuxroot`, ext4, panel
   `GTS9P_ANA38407_AMSA24VU05`, and `lcd_id=800005`. The previous committed
   command line accidentally combined an X710 panel identifier and SD-card
   UUID with an X810 boot bundle. `tools/validate-x810-cmdline.py` rejects that
   class of mismatch before bundle generation. This is a host-side correction,
   not a new tablet flash.
5. Recovery remains user-directed. Do not auto-enter Download Mode or reboot;
   do not patch/replace TWRP as an unasked side effect. The user must retain a
   verified way to restore the correct X810 boot set before switching.
6. Any future release/rootfs/boot installation write must stop unless the
   wizard positively identifies SM-X810, sees one expected TWRP target,
   validates the exact release manifest, has saved the required recovery
   backups on the PC, and proves the package contract. The separate GPT split
   writer currently validates geometry and saves a GPT-metadata capture, but
   does not meet those broader installer gates and therefore remains
   unvalidated. Any destructive step needs a typed confirmation naming the
   data loss and exact split sizes.

## What is currently implemented

`tools/x810-install` is the first Linux command-line/TUI layer: `doctor` checks
host tools and connected ADB identity without rebooting, and in TWRP reads the
X810 boot-partition sizes plus the userdata/linuxroot layout; `backup-gpt`
captures the six GPT metadata sets and edge sectors onto the PC, validating GPT
CRCs without writing to the tablet; `plan-split` previews exact boundaries
from a saved stock capture; and `split` can display a live plan or, with
explicit destructive confirmation, attempt the narrowly scoped GPT edit.
`guide` explains the intended install stages and destructive data consequence;
`release` checks GitHub for a published X810 installation release. The GPT
capture is deliberately not described as a full device backup; partition
payload/identity backups and a tested restore path are still needed. There is
no `install` command because the matched, automated rootfs/boot install path
has not been implemented and the split writer has not been validated on the
live tablet. See [`../INSTALL.md`](../INSTALL.md).
