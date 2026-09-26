> **Location update:** app source and Fedora/X810 integration now live in the sibling `tab-companion` repository. This file records the original deployment and device-specific boundary; shared app/update design is in `linux-ports-docs/tab-companion.md`.

# Fedora Tab Companion (Galaxy Tab S9+ SM-X810)

This is a Fedora/X810 port of the upstream GTK4 Tab Companion app, not the
X910 Android APK. It preserves the upstream **S Pen**, **Cover keyboard**,
**Haptics**, and **Dualboot** sections while omitting Ubuntu-specific update
and fingerprint pages. The Dualboot page uses this repository's X810 board
check, partition map, image sizes, and manifest; it does not reuse X910
partition assumptions. Only the EF-DX815 cover is identified as supported for
this X810 configuration.

The app is available in Fedora's GNOME Applications list. Its dualboot page
shows the active boot set, offers **Start into this system** for the other
system, reports Linux and Android storage (Android usage is unknown because
userdata is encrypted), and offers quick-settings and authentication toggles.
Changing sets requires authorization unless the owner explicitly enables the
no-password toggle. The privileged helper validates the X810 board, exact
partition sizes, all stored image checksums, and each written partition's
readback hash. It does not reboot automatically; after staging, the owner
chooses when to restart. The switcher changes only the four documented boot
partitions.

Only `boot`, `init_boot`, `vendor_boot`, and `dtbo` are in the switch set. The
saved Fedora set and CYG1 Android images live under
`/var/lib/x810-boot-sets/` on `linuxroot`; both are hash-checked. No image has
been written by installing the app. The owner has confirmed Android boots after
staging CYG1. If a write/readback fails, do not reboot; while Fedora is still
running, select **Restore Fedora boot set**. The four writes are sequential,
not atomic.

`vbmeta`, `super`, `userdata`, `recovery`, GPT/PIT, and all bootloader, modem,
EFS, and calibration partitions are explicitly outside the app's write path.
The current modified `vbmeta` is left as-is. Fedora images are also saved on
`linuxroot`. A standalone TWRP package containing that verified Fedora set is
available both on the PC at `out/X810-restore-Fedora-TWRP.zip` and on the
tablet at `/var/lib/x810-boot-sets/recovery/X810-restore-Fedora-TWRP.zip`.
If Android cannot boot, from a PC use TWRP **ADB Sideload**:

```sh
adb sideload out/X810-restore-Fedora-TWRP.zip
```

The ZIP validates the X810 identity, image hashes, and partition sizes before
writing, then checks readback. It only restores `boot`, `init_boot`,
`vendor_boot`, and `dtbo`; it does not reboot. Do not reboot if it reports an
error. The internal copy is on the ext4 `linuxroot` partition, which can be
mounted from TWRP at `/dev/block/by-name/linuxroot` if needed. This recovery
package has been inspected, checksum-verified, and was installed from TWRP to
restore the Fedora set. The stock Android boot has previously replaced TWRP
with Samsung recovery. Upstream Tab S9 TWRP instructions specify flashing
`repack.zip` from TWRP to stop recovery replacement. A copy is at
`/var/lib/x810-boot-sets/recovery/TWRP-repack.zip` (SHA-256
`262827a28321b5c03f281532c46dc6ce23ec96c269760218ccca226c2f54f06a`), but it
has **not** been flashed or validated on this current setup. The app warns of
this; it deliberately does not write `recovery` or claim to keep TWRP in place.

Implementation: the adapted upstream application is in
`packaging/tab-companion-x810/usr/`; `tools/tab-companion-boot-switch` is its
X810-validating UI adapter, `tools/x810-boot-switch-core` is the root writer,
and `tools/tab-companion-boot-status` provides read-only status. The two local
polkit policies and `tools/tab-companion-boot-noask` provide authorization and
the opt-in no-password setting. Fedora's `mate-polkit` agent is installed and
running for graphical authorization. The dualboot and haptics shell extension
UUIDs are enabled in the user's GNOME settings; extensions installed after a
GNOME Shell session starts may not be discoverable until the next GNOME login.
