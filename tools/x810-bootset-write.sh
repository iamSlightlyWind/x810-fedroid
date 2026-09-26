#!/usr/bin/env bash
# Write or restore only the four OS boot-set partitions, from TWRP, no reboot.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
serial=${ADB_SERIAL:-R52W7082ELA}
mode=${1:-}
case "$mode" in
		--smoke) src="$root/work/x810-smoke/out" ;;
		--smoke-next) src="$root/work/x810-smoke/out-next" ;;
		--smoke-console) src="$root/work/x810-smoke/out-console-diagnostic" ;;
		--smoke-release-console) src="$root/work/x810-smoke/out-release-console" ;;
	--android-cyg1) src="$root/work/android-cyg1-boot-set" ;;
	--restore) src="$root/probes/android-baseline/live-boot-images" ;;
	*) echo "usage: ADB_SERIAL=<serial> $0 --smoke|--smoke-next|--smoke-console|--smoke-release-console|--android-cyg1|--restore" >&2; exit 2 ;;
esac

adb -s "$serial" get-state 2>/dev/null | grep -qx recovery || {
	echo "refusing write: $serial is not in recovery" >&2; exit 1;
}
[[ "$(adb -s "$serial" shell getprop ro.twrp.version | tr -d '\r')" == 3.7.1_12-0 ]] || {
	echo 'refusing write: expected TWRP 3.7.1_12-0' >&2; exit 1;
}
[[ "$(adb -s "$serial" shell cat /proc/cmdline | tr -d '\r')" == *SM-X810* ]] || {
	# TWRP properties identify the recovery build generically; cmdline from the
	# active recovery must still carry Samsung's X810 model identity.
	adb -s "$serial" shell grep -q 'SM-X810' /tmp/recovery.log 2>/dev/null || {
		echo 'refusing write: no X810 recovery identity found' >&2; exit 1;
	}
}

if [[ "$mode" == --restore ]]; then
	(cd "$src" && sha256sum -c SHA256SUMS >/dev/null)
else
	(cd "$src" && sha256sum -c SHA256SUMS >/dev/null)
fi

declare -A sizes=( [boot]=100663296 [init_boot]=8388608 \
	[vendor_boot]=100663296 [dtbo]=16777216 )
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for part in boot init_boot vendor_boot dtbo; do
	image="$src/$part.img"
	[[ "$(stat -c %s "$image")" == "${sizes[$part]}" ]] || {
		echo "refusing write: wrong $part image size" >&2; exit 1;
	}
	remote_size=$(adb -s "$serial" shell "blockdev --getsize64 /dev/block/by-name/$part" | tr -d '\r')
	[[ "$remote_size" == "${sizes[$part]}" ]] || {
		echo "refusing write: $part device size mismatch ($remote_size)" >&2; exit 1;
	}
	sha=$(sha256sum "$image" | cut -d' ' -f1)
	rmtmp="/tmp/x810-$part.img"
	adb -s "$serial" push "$image" "$rmtmp" >/dev/null
	[[ "$(adb -s "$serial" shell stat -c %s "$rmtmp" | tr -d '\r')" == "${sizes[$part]}" ]] || {
		echo "staged $part size mismatch; no partition write attempted" >&2; exit 1;
	}
	adb -s "$serial" exec-out sh -c "cat '$rmtmp'" > "$tmp/$part.staged"
	[[ "$(sha256sum "$tmp/$part.staged" | cut -d' ' -f1)" == "$sha" ]] || {
		echo "staged $part hash mismatch; no partition write attempted" >&2; exit 1;
	}
	adb -s "$serial" shell "dd if='$rmtmp' of=/dev/block/by-name/$part bs=4M conv=fsync && sync && rm -f '$rmtmp'"
	adb -s "$serial" exec-out sh -c "dd if=/dev/block/by-name/$part bs=4M 2>/dev/null" > "$tmp/$part.readback"
	[[ "$(sha256sum "$tmp/$part.readback" | cut -d' ' -f1)" == "$sha" ]] || {
		echo "$part readback mismatch; tablet remains in TWRP; do not reboot" >&2; exit 1;
	}
	echo "$part verified"
done
echo "four-partition set $mode complete; still in TWRP; not rebooted"
