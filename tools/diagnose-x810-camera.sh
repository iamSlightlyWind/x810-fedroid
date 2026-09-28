#!/usr/bin/env bash
# Read-only snapshot for diagnosing intermittent X810 camera enumeration.
# Run as the desktop user, once when cameras work and again when they vanish.
set -uo pipefail

usage() {
	cat <<'USAGE'
Usage: diagnose-x810-camera.sh LABEL [OUTPUT_DIR]

Collects kernel camera errors, HI1337/DW9808 I2C binding, V4L2/media nodes,
media-controller topology, libcamera enumeration, node permissions and the
current user's video-group membership. It also saves a short full-debug
libcamera enumeration log and relevant kernel journal/dmesg messages. It does
not stream/capture frames, change hardware state, restart services, or rebind
drivers.

Example:
  tools/diagnose-x810-camera.sh working
  tools/diagnose-x810-camera.sh missing
  diff -u ~/x810-camera-diagnostics/working.txt \
          ~/x810-camera-diagnostics/missing.txt
USAGE
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then usage; exit 0; fi
if [[ $# -lt 1 || $# -gt 2 || ! ${1:-} =~ ^[A-Za-z0-9._-]+$ ]]; then
	usage >&2
	exit 2
fi

label=$1
outdir=${2:-"${HOME:-/tmp}/x810-camera-diagnostics"}
mkdir -p -- "$outdir" || exit 1
outfile="$outdir/$label.txt"
exec > >(tee "$outfile") 2>&1

section() { printf '\n===== %s =====\n' "$*"; }
run() {
	printf '+ '
	printf '%q ' "$@"
	printf '\n'
	if command -v timeout >/dev/null 2>&1; then
		timeout 20s "$@" 2>&1 || printf '[exit %s]\n' "$?"
	else
		"$@" 2>&1 || printf '[exit %s]\n' "$?"
	fi
}

section "Snapshot"
printf 'label=%s\n' "$label"
date --iso-8601=seconds 2>/dev/null || date
printf 'uptime='; cat /proc/uptime 2>/dev/null || true
printf 'boot_id='; cat /proc/sys/kernel/random/boot_id 2>/dev/null || true
printf 'kernel='; uname -a

section "Live firmware device-tree camera nodes"
dt_root=/sys/firmware/devicetree/base
if [[ -r $dt_root/model ]]; then
	printf 'model='; tr '\0' ' ' < "$dt_root/model"; echo
else
	echo 'Firmware device-tree model is unavailable.'
fi
if [[ -d $dt_root ]]; then
	found=0
	while IFS= read -r -d '' compatible_file; do
		compatible=$(tr '\0' ' ' < "$compatible_file" 2>/dev/null) || continue
		case "$compatible" in
			*hynix,hi1337-gts9u-rear*|*hynix,hi1337-gts9u-front*) ;;
			*) continue ;;
		esac
		found=1
		node=${compatible_file%/compatible}
		node_path=${node#"$dt_root"}
		[[ -n $node_path ]] || node_path=/
		printf '%s compatible=%s' "$node_path" "$compatible"
		if [[ -r $node/status ]]; then
			printf ' status='
			tr -d '\0' < "$node/status"
		else
			printf ' status=<implicit okay or unavailable>'
		fi
		if [[ -r $node/reg ]]; then
			printf ' reg='
			od -An -tx1 "$node/reg" | tr -d ' \n'
		else
			printf ' reg=<missing>'
		fi
		echo
	done < <(find "$dt_root" -type f -name compatible -print0 2>/dev/null)
	(( found )) || echo 'No X810 HI1337 rear/front compatible nodes found in the live firmware tree.'
else
	echo 'Firmware device tree is unavailable at /sys/firmware/devicetree/base.'
fi

section "Desktop-user access"
id
getent group video 2>/dev/null || true

section "Relevant loaded drivers"
for module in qcom_camss qcom_cci hi1337_gts9u dw9808_vcm; do
	if [[ -d "/sys/module/$module" ]]; then printf '%s: loaded\n' "$module"
	else printf '%s: not listed in /sys/module (may be built-in)\n' "$module"; fi
done

section "I2C adapters, including CCI"
for adapter in /sys/bus/i2c/devices/i2c-*; do
	[[ -e $adapter ]] || continue
	name=''
	[[ -r $adapter/name ]] && name=$(<"$adapter/name")
	real_adapter=$(readlink -f "$adapter")
	driver='<not exposed on adapter node>'
	for candidate in "$real_adapter/driver" "$(dirname "$real_adapter")/driver"; do
		if [[ -L $candidate ]]; then driver=$(basename "$(readlink -f "$candidate")"); break; fi
	done
	of_node='<none>'
	for candidate in "$real_adapter/of_node" "$(dirname "$real_adapter")/of_node"; do
		if [[ -e $candidate ]]; then of_node=$(readlink -f "$candidate"); break; fi
	done
	printf '%s name=%s driver=%s of_node=%s\n' "${adapter##*/}" "${name:-<no name>}" "$driver" "$of_node"
done

section "HI1337 and DW9808 I2C devices"
found=0
for device in /sys/bus/i2c/devices/*; do
	[[ -e $device ]] || continue
	name=''
	[[ -r $device/name ]] && name=$(<"$device/name")
	compatible=''
	[[ -r $device/of_node/compatible ]] && compatible=$(tr '\0' ' ' < "$device/of_node/compatible")
	case "$name $compatible ${device##*/}" in
		*hi1337*|*HI1337*|*dw9808*|*DW9808*)
			found=1
			printf '%s name=%s\n' "$device" "${name:-<no name>}"
			if [[ -L $device/driver ]]; then printf '  driver=%s\n' "$(readlink -f "$device/driver")"; else echo '  driver=<unbound>'; fi
			if [[ -r $device/modalias ]]; then printf '  modalias=%s\n' "$(<"$device/modalias")"; fi
			printf '  compatible=%s\n' "${compatible:-<none>}"
			if [[ -e $device/of_node ]]; then printf '  device_tree_node=%s\n' "$(readlink -f "$device/of_node")"; fi
			for property in reg status clock-names assigned-clock-rates; do
				if [[ -r $device/of_node/$property ]]; then
					printf '  of:%s=' "$property"
					od -An -tx1 "$device/of_node/$property" | tr -d '\n'
					echo
				fi
			done
			;;
	esac
done
(( found )) || echo 'No matching sensor/actuator I2C devices found.'

section "V4L2 and media device nodes"
for class in /sys/class/video4linux/* /sys/class/media/*; do
	[[ -e $class ]] || continue
	base=${class##*/}
	name=''
	[[ -r $class/name ]] && name=$(<"$class/name")
	[[ -r $class/model ]] && name="${name:+$name; }$(<"$class/model")"
	node="/dev/$base"
	printf '%s name=%s' "$node" "${name:-<no name>}"
	if [[ -e $node ]]; then
		stat -c ' mode=%a owner=%U group=%G major:minor=%t:%T' "$node" 2>&1 | tr -d '\n'
		printf ' readable=%s writable=%s' "$([[ -r $node ]] && echo yes || echo no)" "$([[ -w $node ]] && echo yes || echo no)"
	else
		printf ' node=missing'
	fi
	echo
done

section "V4L2 persistent aliases"
if [[ -d /dev/v4l ]]; then
	find /dev/v4l -maxdepth 2 -type l -printf '%p -> %l\n' 2>&1 | sort
else
	echo '/dev/v4l is absent'
fi

section "V4L2 inventory"
if command -v v4l2-ctl >/dev/null 2>&1; then run v4l2-ctl --list-devices; else echo 'v4l2-ctl not installed'; fi

section "Media-controller topology"
if command -v media-ctl >/dev/null 2>&1; then
	for node in /dev/media*; do [[ -e $node ]] && run media-ctl -d "$node" --print-topology; done
else
	echo 'media-ctl not installed'
fi

section "libcamera enumeration"
if command -v cam >/dev/null 2>&1; then
	libcamera_log="$outdir/$label-libcamera.log"
	printf '+ LIBCAMERA_LOG_LEVELS=*:DEBUG LIBCAMERA_LOG_FILE=%q cam -l (timeout 20s)\n' "$libcamera_log"
	if command -v timeout >/dev/null 2>&1; then
		timeout 20s env LIBCAMERA_LOG_LEVELS='*:DEBUG' \
			LIBCAMERA_LOG_FILE="$libcamera_log" cam -l 2>&1 || printf '[exit %s]\n' "$?"
	else
		LIBCAMERA_LOG_LEVELS='*:DEBUG' LIBCAMERA_LOG_FILE="$libcamera_log" cam -l 2>&1 || printf '[exit %s]\n' "$?"
	fi
	if [[ -s $libcamera_log ]]; then
		echo "Debug log: $libcamera_log"
		echo 'Last 300 libcamera log lines:'
		tail -n 300 "$libcamera_log"
	else
		echo "No libcamera debug log produced at $libcamera_log"
	fi
else
	echo 'cam (libcamera-tools) not installed'
fi

section "PipeWire camera monitor / portal hints"
if command -v systemctl >/dev/null 2>&1; then
	run systemctl --user --no-pager --full status pipewire.service wireplumber.service
fi
if command -v journalctl >/dev/null 2>&1; then
	run journalctl --user --no-pager -b -o short-monotonic -u pipewire.service -u wireplumber.service
fi
if command -v wpctl >/dev/null 2>&1; then run wpctl status -n; else echo 'wpctl not installed'; fi

section "Camera-related kernel log from this boot"
if command -v journalctl >/dev/null 2>&1 && journalctl --quiet --no-pager -b -k -o short-monotonic >/dev/null 2>&1; then
	echo 'Kernel warnings/errors (all subsystems; camera regulator/CCI suppliers may not mention camera):'
	journalctl --no-pager -b -k -o short-monotonic -p warning..alert 2>&1 || true
	echo 'Camera/CCI/probe events with three lines of boot-time context:'
	journalctl --no-pager -b -k -o short-monotonic 2>&1 |
		grep -Ei -C 3 'hi1337|dw9808|camss|camcc|csiphy|csi.?phy|vfe|cci|camera|media.*notifier|notifier.*(bound|complete|fail)|probe.*defer|defer.*probe|regulator.*(fail|error)|clock.*(fail|error)' || echo 'No matching camera/kernel messages.'
elif command -v dmesg >/dev/null 2>&1 && dmesg --ctime >/dev/null 2>&1; then
	echo 'Kernel journal unavailable to this user; using readable dmesg ring buffer instead (may not retain the full boot).'
	dmesg --ctime 2>&1 | grep -Ei -C 3 'hi1337|dw9808|camss|camcc|csiphy|csi.?phy|vfe|cci|camera|media.*notifier|notifier.*(bound|complete|fail)|probe.*defer|defer.*probe|regulator.*(fail|error)|clock.*(fail|error)' || echo 'No matching camera/kernel messages.'
else
	echo 'Kernel logs are not readable by this user; attach `sudo journalctl -b -k -o short-monotonic -p warning..alert` from this boot.'
fi

section "End"
printf 'Saved %s\n' "$outfile"
