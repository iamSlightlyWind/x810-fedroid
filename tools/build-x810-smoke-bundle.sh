#!/usr/bin/env bash
# Assemble a non-persistent X810 Linux bring-up bundle. This does not flash.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
repo="$root/work/upstream/gts9wifi-fedora-linux"
live="$root/probes/android-baseline/live-boot-images"
kernel="$root/work/x810-smoke/Image.gz"
dtb="$root/work/kernel-source/x810-memory-audit-candidate.dtb"
dtbo=
dtbo_fallback=0
busybox=
out="$root/work/x810-smoke/out"

while (($#)); do
	case "$1" in
		--busybox) busybox="$2"; shift 2 ;;
		--kernel) kernel="$2"; shift 2 ;;
		--dtb) dtb="$2"; shift 2 ;;
		--dtbo) dtbo="$2"; shift 2 ;;
		--abl-dtbo-fallback) dtbo_fallback=1; shift ;;
		--out) out="$2"; shift 2 ;;
		*) echo "unknown argument: $1" >&2; exit 2 ;;
	esac
done

[[ -n "$busybox" && -x "$busybox" ]] || { echo 'pass --busybox <static aarch64 busybox>' >&2; exit 2; }
for f in "$kernel" "$dtb" "$dtbo" "$live/vendor_boot.img"; do
	[[ -z "$f" || -f "$f" ]] || { echo "missing input: $f" >&2; exit 2; }
done
for tool in cpio gzip lz4 unpack_bootimg fdtget fdtput; do
	command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 2; }
done

# Verify this is the X810 board tuple before packaging it.
[[ "$(fdtget -t s "$dtb" / model)" == 'Samsung GTS9PWIFI PROJECT (board-id,04)' ]]
[[ "$(fdtget -t x "$dtb" / qcom,board-id)" == '10008 4' ]]

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if ((dtbo_fallback)); then
	# Follow the physically tested gts9wifi bundle: an invalid 4 KiB DTBO
	# makes Samsung ABL use the appended/vendor_boot DTB instead of overlaying
	# Android DTBO fragments onto this mainline-derived tree.
	mkdir -p "$(dirname "$out")"
	truncate -s 4096 "$tmp/x810-abl-fallback-dtbo.img"
	dtbo="$tmp/x810-abl-fallback-dtbo.img"
elif [[ -z "$dtbo" ]]; then
	python3 "$root/work/kernel-source/mkdtboimg-aosp.py" create \
		"$tmp/x810-noop-dtbo.img" --page_size=4096 \
		"$root/work/kernel-source/x810-board00-noop.dtbo" \
		"$root/work/kernel-source/x810-board02-noop.dtbo" \
		"$root/work/kernel-source/x810-board04-noop.dtbo"
	dtbo="$tmp/x810-noop-dtbo.img"
fi
if ((!dtbo_fallback)); then python3 - "$dtbo" <<'PY'
import struct, sys
b = open(sys.argv[1], 'rb').read()
magic, total, hdr, ent, count, off, page, version = struct.unpack_from('>8I', b)
if magic != 0xd7b7ab1e or total != len(b) or hdr != 32 or ent != 32 \
        or count != 3 or page != 4096:
    raise SystemExit('invalid X810 selector DTBO table')
for i in range(count):
    size, offset = struct.unpack_from('>2I', b, off + i * ent)
    if offset < hdr + count * ent or offset + size > total:
        raise SystemExit(f'invalid DTBO entry {i}')
PY
fi
mkdir -p "$tmp/root/bin" "$tmp/root/dev" "$tmp/root/proc" "$tmp/root/sys" \
	"$tmp/root/tmp" "$tmp/root/etc" "$tmp/empty"
install -m0755 "$busybox" "$tmp/root/bin/busybox"
for app in sh mount mkdir ln sleep echo cat grep sync setsid cttyhack uname basename mknod; do
	ln -s busybox "$tmp/root/bin/$app"
done
install -m0755 "$root/tools/x810-smoke-init" "$tmp/root/init"

(cd "$tmp/root" && find . -print0 | cpio --reproducible --null --owner=0:0 \
	-o --format=newc 2>/dev/null) | gzip -n -9 > "$tmp/smoke.cpio.gz"
(cd "$tmp/empty" && find . -print0 | cpio --reproducible --null --owner=0:0 \
	-o --format=newc 2>/dev/null) | lz4 -l -12 - "$tmp/empty.lz4" >/dev/null
gzip -dc "$tmp/smoke.cpio.gz" | lz4 -l -12 - "$tmp/smoke.lz4" >/dev/null

mkdir -p "$tmp/vendor"
vendor_info=$(unpack_bootimg --boot_img "$live/vendor_boot.img" --out "$tmp/vendor")
stock_cmdline=$(sed -n 's/^vendor command line args: //p' <<<"$vendor_info")
[[ -n "$stock_cmdline" && -s "$tmp/vendor/bootconfig" ]]
# Match the proven SM-X910 mainline debug command line rather than retaining
# Android's video=vfb/firmware-loader arguments. Keep bootloader-enabled
# clocks, power domains and regulators alive, and expose framebuffer/UART
# consoles plus verbose initcall progress.
cmdline='console=tty0 console=ttyMSM0,115200n8 ignore_console_null earlycon loglevel=8 log_buf_len=4M clk_ignore_unused pd_ignore_unused regulator_ignore_unused rdinit=/init'

# Do not let an X710-derived display/PCIe configuration exercise X810 hardware
# in the initial console-only smoke boot.
cp "$dtb" "$tmp/x810-smoke.dtb"
for node in /soc@0/display-subsystem@ae00000 /soc@0/pcie@1c00000 \
	/regulator-display-avdd /regulator-panel-ldo; do
	fdtput -t s "$tmp/x810-smoke.dtb" "$node" status disabled
done
# The diagnostic console must work with a host cable present even if the
# Samsung Type-C role-switch driver is late or declines the switch. Keep this
# one-boot smoke DTB in peripheral mode and bypass dynamic role switching.
fdtput -t s "$tmp/x810-smoke.dtb" /soc@0/usb@a600000 dr_mode peripheral
fdtput -d "$tmp/x810-smoke.dtb" /soc@0/usb@a600000 usb-role-switch

mkdir -p "$out"
rm -f "$out"/*.img "$out"/SHA256SUMS "$out"/BUILD-METADATA.txt

# ABL on the X710 sibling expects a gzip-compressed ARM64 kernel payload; keep
# that proven loader convention and append the DTB exactly as its bundle does.
# The X810-specific DTB also goes in vendor_boot, where ABL loads the active FDT.
# A Qualcomm-metadata no-op DTBO table retains the selector shape without
# applying X810 Android overlays to Linux.
gzip -t "$kernel"
python3 - "$kernel" <<'PY'
import gzip, sys
b = gzip.decompress(open(sys.argv[1], 'rb').read())
if len(b) < 64 or b[0x38:0x3c] != b'ARMd':
    raise SystemExit('kernel gzip does not contain a valid arm64 Image')
PY
cat "$kernel" "$tmp/x810-smoke.dtb" > "$tmp/Image-dtb"
python3 "$repo/tools/mkbootimg.py" \
	--kernel "$tmp/Image-dtb" --cmdline '' --header_version 4 \
	--os_version 13 --os_patch_level 2025-07 --pagesize 4096 \
	--base 0x80000000 --kernel_offset 0x8000 --tags_offset 0x01e00000 \
	-o "$out/boot.img"
python3 "$repo/tools/mkbootimg.py" \
	--ramdisk "$tmp/smoke.lz4" --header_version 4 --pagesize 4096 \
	-o "$out/init_boot.img"
python3 "$repo/tools/mkbootimg.py" \
	--ramdisk_type platform --ramdisk_name x810-smoke \
	--vendor_ramdisk_fragment "$tmp/smoke.lz4" \
	--dtb "$tmp/x810-smoke.dtb" --vendor_cmdline "$cmdline" \
	--vendor_bootconfig "$tmp/vendor/bootconfig" --header_version 4 \
	--vendor_boot "$out/vendor_boot.img" --base 0x80000000 \
	--kernel_offset 0x8000 --ramdisk_offset 0x02000000 \
	--tags_offset 0x01e00000 --pagesize 4096 --dtb_offset 0x1f00000
cp "$dtbo" "$out/dtbo.img"

for spec in 'boot.img:100663296' 'init_boot.img:8388608' \
	'vendor_boot.img:100663296' 'dtbo.img:16777216'; do
	name=${spec%%:*}; size=${spec##*:}
	python3 "$repo/tools/avbtool" add_hash_footer --image "$out/$name" \
		--partition_name "${name%.img}" --partition_size "$size" \
		--salt "$(sha256sum "$out/$name" | cut -d' ' -f1)"
	[[ "$(stat -c %s "$out/$name")" == "$size" ]]
done

(cd "$out" && sha256sum boot.img init_boot.img vendor_boot.img dtbo.img > SHA256SUMS)
{
	printf 'kernel_gzip_sha256=%s\n' "$(sha256sum "$kernel" | cut -d' ' -f1)"
	printf 'dtb_sha256=%s\n' "$(sha256sum "$tmp/x810-smoke.dtb" | cut -d' ' -f1)"
	printf 'dtbo_sha256=%s\n' "$(sha256sum "$dtbo" | cut -d' ' -f1)"
	printf 'busybox_sha256=%s\n' "$(sha256sum "$busybox" | cut -d' ' -f1)"
	printf 'bootconfig_source=CYG1 live vendor_boot, preserved\n'
	printf 'avb_note=per-image hash footers only; vbmeta unchanged/not included\n'
	printf 'initramfs=static BusyBox in vendor_boot platform fragment (LZ4 legacy); empty init_boot; printk persisted to /cache/x810-linux-probe/boot.log; USB ACM shell\n'
	printf 'disabled_for_smoke=mdss, pcie0, display_panel_avdd, panel_ldo_en\n'
	printf 'usb_smoke_mode=peripheral, role-switch disabled\n'
	printf 'console_debug=X910-style tty0+ttyMSM0+earlycon; ignore_console_null; loglevel=8; no initcall_debug or CPU cap; keep firmware clocks/power/regulators; rdinit=/init diagnostic\n'
	if ((dtbo_fallback)); then printf 'dtbo_strategy=invalid 4KiB image; ABL fallback to bundled DTB\n'; fi
} > "$out/BUILD-METADATA.txt"
echo "Experimental X810 smoke bundle built (not flashed): $out"
