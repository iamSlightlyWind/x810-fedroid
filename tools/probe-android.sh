#!/usr/bin/env bash
# Read-only inventory for a Samsung gts9pwifi. Does not reboot or write to device.
set -euo pipefail
SERIAL=${ADB_SERIAL:-192.168.1.11:5555}
OUT=${1:-probes/android-baseline}
ADB=${ADB:-adb}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$OUT"
run() { "$ADB" -s "$SERIAL" shell "su -c '$1'"; }
printf 'timestamp=%s\nserial=%s\n' "$(date -Is)" "$SERIAL" > "$OUT/capture-meta.txt"
"$ADB" devices -l > "$OUT/adb-devices.txt"
"$ADB" -s "$SERIAL" shell getprop > "$OUT/getprop.txt"
run 'id; getenforce; cat /proc/version; cat /proc/cmdline; cat /proc/bootconfig' > "$OUT/boot-state.txt" 2>&1 || true
run 'cat /proc/iomem; cat /proc/meminfo' > "$OUT/memory.txt" 2>&1 || true
run 'cat /proc/partitions; cat /proc/mounts; ls -l /dev/block/by-name; ls -l /dev/block/by-partlabel; ls -l /dev/block/platform/*/by-name 2>/dev/null; ls -l /sys/block; command -v lsblk && lsblk; command -v blkid && blkid; command -v blockdev && for p in /dev/block/by-name/*; do [ -b "$p" ] && printf "%s " "$p" && blockdev --getsize64 "$p"; done' > "$OUT/block-layout.txt" 2>&1 || true
run 'for d in sda sdb sdc sdd sde sdf; do echo ===$d; blockdev --getsize64 /dev/block/$d; blockdev --getro /dev/block/$d; readlink -f /sys/block/$d/device; cat /sys/block/$d/queue/logical_block_size /sys/block/$d/queue/physical_block_size; done; for n in /dev/block/by-name/*; do [ -L "$n" ] && printf "%s %s\n" "${n##*/}" "$(readlink -f "$n")"; done' > "$OUT/ufs-lun-summary.txt" 2>&1 || true
run 'for d in /sys/bus/i2c/devices /sys/bus/spi/devices /sys/bus/pci/devices /sys/bus/platform/devices /sys/class/input; do echo ===$d; ls -l "$d"; done; cat /proc/bus/input/devices' > "$OUT/devices-and-input.txt" 2>&1 || true
run 'for p in /sys/bus/pci/devices/*; do echo ===$p; for f in vendor device subsystem_vendor subsystem_device class modalias; do printf "%s=" "$f"; cat "$p/$f" 2>/dev/null; done; readlink -f "$p/driver"; done' > "$OUT/pci-ids.txt" 2>&1 || true
run 'ls -l /sys/firmware/devicetree/base; ls -l /sys/firmware/devicetree/base/reserved-memory; dmesg' > "$OUT/dt-and-dmesg.txt" 2>&1 || true
run 'cat /proc/config.gz 2>/dev/null | gzip -dc' > "$OUT/kernel.config" 2>&1 || true
run 'for p in /sys/fs/pstore/*; do [ -e "$p" ] && { echo ===$p; cat "$p" 2>/dev/null; }; done; getprop sys.boot.reason; getprop ro.boot.bootreason' > "$OUT/pstore-bootreason.txt" 2>&1 || true
"$ADB" -s "$SERIAL" exec-out su -c 'tar -C /sys/firmware/devicetree/base -cf - .' > "$OUT/live-device-tree.tar"
# Preserve read-only copies of the active chain plus the recovery/parameter
# partitions before any future design can consider writing them. This is a
# host-side read and never writes to or reboots the tablet.
mkdir -p "$OUT/live-boot-images"
for part in boot init_boot vendor_boot dtbo vbmeta recovery param; do
	expected=$(run "blockdev --getsize64 /dev/block/by-name/$part" | tr -d '\r')
	tmp="$OUT/live-boot-images/.$part.img.partial"
	"$ADB" -s "$SERIAL" exec-out su -c "dd if=/dev/block/by-name/$part bs=4M 2>/dev/null" > "$tmp"
	actual=$(wc -c < "$tmp")
	[ "$actual" -eq "$expected" ] || { echo "$part capture truncated: $actual vs $expected" >&2; exit 1; }
	mv "$tmp" "$OUT/live-boot-images/$part.img"
done
(cd "$OUT/live-boot-images" && sha256sum -- *.img > SHA256SUMS)
# GPT metadata is read-only. These are first/last 1 MiB edge captures for each
# UFS LU, not full-device backups. They are written to the host OUT directory.
for disk in sda sdb sdc sdd sde sdf; do
	"$ADB" -s "$SERIAL" exec-out su -c "dd if=/dev/block/$disk bs=4096 count=256 2>/dev/null" > "$OUT/$disk-first-1m.bin"
	bytes=$(run "blockdev --getsize64 /dev/block/$disk" | tr -d '\r')
	# Calculate on the host: Android mksh's integer arithmetic overflows on sda.
	skip=$((bytes / 4096 - 256))
	"$ADB" -s "$SERIAL" exec-out su -c "dd if=/dev/block/$disk bs=4096 skip=$skip count=256 2>/dev/null" > "$OUT/$disk-last-1m.bin"
done
python3 "$SCRIPT_DIR/parse-gpt-capture.py" "$OUT" > "$OUT/gpt-summary.txt"
(cd "$OUT" && { for file in *; do
	[ -f "$file" ] || continue
	[ "$file" = SHA256SUMS ] && continue
	sha256sum -- "$file"
done; } > SHA256SUMS)
echo "Read-only capture complete: $OUT"
