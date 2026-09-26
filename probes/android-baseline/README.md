# Live-device baseline capture

Captured read-only over wireless ADB from `192.168.1.11:5555` on 2026-09-24.
The raw capture is intentionally git-ignored: it contains full Android
properties, device-tree data, kernel logs and hardware identifiers. Keep it on
the development host and handle it as potentially sensitive. Re-run
`tools/probe-android.sh` to refresh it. The script does not reboot or write to
the tablet. `live-boot-images/` contains host-side read-only copies of the live
boot set plus `vbmeta`, `recovery`, and `param`; its `SHA256SUMS` file is
generated only after byte-size checks. The root `SHA256SUMS` covers the other
files in this directory (not this checksum file or subdirectories).

`recovery-readonly/` is a separate read-only capture from USB-connected TWRP
on 2026-09-24. It contains `/proc/memsize/reserved`, `/proc/meminfo`, and
`/proc/iomem`. Samsung's `%pK` formatting hides pool base/end addresses while
`kptr_restrict=2`, but the named pool sizes and allocation classes remain
visible. This capture is from recovery, not a newly booted Android session.
