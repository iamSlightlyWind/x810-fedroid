#!/usr/bin/env bash
# Build the updater-installable Fedora support RPM from the X810 rootfs overlay,
# its pinned SSC-backed sensor proxy stack, and the Fedora 44 aarch64 HI1337 IPA.
# The kernel, boot chain, and firmware remain excluded.
set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 5 ]; then
	echo "usage: $0 ROOTFS_DIR PORT_VERSION OUT_DIR [RPM_RELEASE] [BUILD_INFO_JSON]" >&2
	exit 2
fi

rootfs="$(realpath "$1")"
version="$2"
outdir="$(realpath -m "$3")"
rpm_release="${4:-1}"
build_info="${5:-$rootfs/usr/share/tab-companion/port-build.json}"
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"

if [[ ! "$version" =~ ^(0|[1-9][0-9]{0,19})\.(0|[1-9][0-9]{0,19})\.(0|[1-9][0-9]{0,19})$ ]]; then
	echo "build-port-support-rpm: refusing unknown or malformed port version" >&2
	exit 2
fi
if [[ ! "$rpm_release" =~ ^[A-Za-z0-9][A-Za-z0-9._+~-]{0,63}$ ]]; then
	echo "build-port-support-rpm: refusing malformed RPM release" >&2
	exit 2
fi
if [ ! -d "$rootfs" ] || [ ! -d "$repo_dir/rootfs/overlay" ] || [ ! -f "$repo_dir/specs/x810-fedora-port.spec" ]; then
	echo "build-port-support-rpm: rootfs or overlay/spec is missing" >&2
	exit 2
fi
if { [ "$#" -eq 5 ] || [ -e "$build_info" ]; } && { [ ! -f "$build_info" ] || [ -L "$build_info" ]; }; then
	echo "build-port-support-rpm: build-info must be a regular, non-symlink file" >&2
	exit 2
fi
if ! command -v rpmbuild >/dev/null || ! command -v rpm >/dev/null || ! command -v readelf >/dev/null; then
	echo "build-port-support-rpm: rpmbuild, rpm, and readelf are required (install rpm-build and binutils)" >&2
	exit 2
fi

python3 - "$rootfs" "$version" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]) / "usr/share/tab-companion/port.json"
doc = json.loads(path.read_text(encoding="utf-8"))
expected = {
    "schema_version": 1,
    "port_id": "x810-fedora",
    "device_id": "SM-X810",
    "os_id": "fedora",
    "arch": "aarch64",
}
if not isinstance(doc, dict) or any(doc.get(k) != v for k, v in expected.items()):
    raise SystemExit("build-port-support-rpm: rootfs port.json target/schema mismatch")
if doc.get("version") != sys.argv[2]:
    raise SystemExit("build-port-support-rpm: rootfs port.json version mismatch")
PY

if [ -f "$build_info" ]; then
	python3 - "$build_info" "$version" <<'PY'
import json
import re
import sys
from pathlib import Path

doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(doc, dict) or doc.get("schema_version") != 1 \
        or doc.get("project") != "x810-fedora" or doc.get("version") != sys.argv[2]:
    raise SystemExit("build-port-support-rpm: build-info identity/version mismatch")
for key in ("run_id", "run_number"):
    if isinstance(doc.get(key), bool) or not isinstance(doc.get(key), int) or doc[key] < 0:
        raise SystemExit(f"build-port-support-rpm: invalid build-info {key}")
if doc["run_id"] == 0 and doc["run_number"] != 0:
    raise SystemExit("build-port-support-rpm: run-number must be zero for an unbuilt baseline")
if doc["run_id"] > 0:
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(doc.get("commit", ""))) \
            or not isinstance(doc.get("branch"), str) or not doc["branch"]:
        raise SystemExit("build-port-support-rpm: incomplete Actions build identity")
PY
fi

mkdir -p "$outdir"
work="$(mktemp -d "${TMPDIR:-/tmp}/x810-port-rpm.XXXXXXXX")"
trap 'rm -rf "$work"' EXIT
top="$work/rpmbuild"
stage="$work/payload"
mkdir -p "$top/BUILD" "$top/BUILDROOT" "$top/RPMS" \
	"$top/SOURCES" "$top/SPECS" "$top/SRPMS" "$stage"

# Stage only the repository-owned overlay. Use the already-stamped rootfs
# port.json instead of the source overlay's deliberately-unknown template.
cp -a "$repo_dir/rootfs/overlay/." "$stage/"
# Use the same source locks, patches and builder as the fresh rootfs. This
# makes the slow-SSC/auto-rotation fix reach existing installs through the
# existing single-RPM Tab Companion update channel.
bash "$repo_dir/tools/build-x810-sensor-proxy.sh" "$stage" >&2
# Fedora's systemd package owns these two machine-local configuration files.
# They are staged into a fresh image, but claiming them from this separate
# updater RPM creates duplicate ownership and breaks contract verification (or
# later updates). Keep the tablet identity in the image only; leave Fedora's
# locale default entirely alone.
rm -f "$stage/etc/machine-info" "$stage/etc/locale.conf"
install -Dm0644 "$rootfs/usr/share/tab-companion/port.json" \
	"$stage/usr/share/tab-companion/port.json"
if [ -f "$build_info" ]; then
	install -Dm0644 "$build_info" "$stage/usr/share/tab-companion/port-build.json"
fi
# The custom software IPA must be a native aarch64 module, not mislabeled as
# noarch. It lives beside (not over) Fedora's RPM-owned module, and the global
# libcamera configuration selects it first. The generated private build
# signature is deliberately not staged: Fedora's libcamera will isolate this
# port-specific module through its stock soft_ipa_proxy.
# This script is called through command substitution by the rootfs builder,
# whose stdout contract is a single RPM pathname. Keep verbose compiler and
# Meson output on stderr so it cannot be mistaken for that pathname.
bash "$repo_dir/tools/build-libcamera-hi1337-ipa.sh" "$stage" >&2
plugin="$stage/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so"
plugin_machine="$(readelf -h "$plugin" | sed -n 's/^[[:space:]]*Machine:[[:space:]]*//p')"
if [ "$plugin_machine" != AArch64 ]; then
	echo "build-port-support-rpm: libcamera IPA is not an aarch64 binary ($plugin_machine)" >&2
	exit 1
fi
tar -C "$stage" -czf "$top/SOURCES/port-overlay.tar.gz" .
(cd "$stage" && find . \( -type f -o -type l \) -printf '/%P\n' \
	| grep -v '^/usr/share/licenses/x810-fedora-port/' | LC_ALL=C sort) \
	> "$top/SOURCES/port-overlay.filelist"
install -m0644 "$repo_dir/LICENSE" "$top/SOURCES/port-license.txt"
install -Dm0644 "$repo_dir/LICENSE" "$stage/usr/share/licenses/x810-fedora-port/LICENSE"
cp "$repo_dir/specs/x810-fedora-port.spec" "$top/SPECS/"

rpmbuild --define "_topdir $top" --define "port_version $version" \
	--define "port_release $rpm_release" \
	-bb "$top/SPECS/x810-fedora-port.spec" >&2

mapfile -t built < <(find "$top/RPMS/aarch64" -maxdepth 1 -type f \
	-name 'x810-fedora-port-*.aarch64.rpm' -print)
if [ "${#built[@]}" -ne 1 ]; then
	echo "build-port-support-rpm: expected exactly one aarch64 RPM" >&2
	exit 1
fi
rpm_path="${built[0]}"
rpm_name="$(rpm -qp --qf '%{NAME}' "$rpm_path")"
rpm_version="$(rpm -qp --qf '%{VERSION}' "$rpm_path")"
rpm_release="$(rpm -qp --qf '%{RELEASE}' "$rpm_path")"
rpm_arch="$(rpm -qp --qf '%{ARCH}' "$rpm_path")"
if [ "$rpm_name" != x810-fedora-port ] || [ "$rpm_version" != "$version" ] || [ "$rpm_arch" != aarch64 ]; then
	echo "build-port-support-rpm: built RPM metadata does not match the release" >&2
	exit 1
fi

# The package must include every staged overlay file and the stamped identity,
# and must never own a kernel, boot image, or firmware path.
rpm -qpl "$rpm_path" | LC_ALL=C sort > "$work/package-files"
comm -23 "$top/SOURCES/port-overlay.filelist" "$work/package-files" > "$work/missing-files"
if [ -s "$work/missing-files" ]; then
	echo "build-port-support-rpm: RPM omits expected overlay files:" >&2
	cat "$work/missing-files" >&2
	exit 1
fi
if ! grep -Fxq '/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so' "$work/package-files" || \
   ! grep -Fxq '/etc/libcamera/configuration.yaml' "$work/package-files" || \
   ! grep -Fxq '/etc/environment.d/90-x810-libcamera.conf' "$work/package-files" || \
   ! grep -Fxq '/etc/environment.d/91-x810-gtk-rendering.conf' "$work/package-files" || \
   ! grep -Fxq '/usr/share/licenses/x810-fedora-port/LICENSE' "$work/package-files" || \
   ! grep -Fxq '/usr/share/licenses/x810-fedora-port/libcamera/LGPL-2.1-or-later.txt' "$work/package-files" || \
   ! grep -Fxq '/usr/share/licenses/x810-fedora-port/libcamera/BSD-2-Clause.txt' "$work/package-files"; then
	echo "build-port-support-rpm: package omits the X810 libcamera helper/configuration" >&2
	exit 1
fi
for required in \
	/usr/libexec/iio-sensor-proxy \
	/usr/bin/monitor-sensor \
	/usr/bin/ssccli \
	/usr/lib64/libssc.so.2 \
	/usr/lib/systemd/system/iio-sensor-proxy.service \
	/usr/lib/udev/rules.d/80-iio-sensor-proxy.rules \
	/usr/share/dbus-1/system.d/net.hadess.SensorProxy.conf \
	/usr/share/polkit-1/actions/net.hadess.SensorProxy.policy; do
	if ! grep -Fxq "$required" "$work/package-files"; then
		echo "build-port-support-rpm: package omits the SSC sensor stack file $required" >&2
		exit 1
	fi
done
if ! grep -Eq '^/usr/lib64/libssc\.so\.[0-9]' "$work/package-files"; then
	echo "build-port-support-rpm: package omits the libssc runtime shared library" >&2
	exit 1
fi
if ! rpm -qp --provides "$rpm_path" | grep -Fxq 'iio-sensor-proxy = 3.9'; then
	echo "build-port-support-rpm: package does not provide the iio-sensor-proxy capability" >&2
	exit 1
fi
if ! rpm -qp --provides "$rpm_path" | grep -Fxq 'libssc.so.2()(64bit)' || \
   ! rpm -qp --requires "$rpm_path" | grep -Fxq 'libssc.so.2()(64bit)'; then
	echo "build-port-support-rpm: package does not expose/require its libssc.so.2 runtime ABI" >&2
	exit 1
fi
if grep -Fxq '/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so.sign' "$work/package-files"; then
	echo "build-port-support-rpm: refusing to ship an IPA signature that does not match Fedora's key" >&2
	exit 1
fi
if grep -Eq '^/(boot|boot/|lib/modules/|usr/lib/modules/|usr/lib/firmware/|lib/firmware/)' "$work/package-files"; then
	echo "build-port-support-rpm: refusing package that contains boot/kernel/firmware files" >&2
	exit 1
fi

destination="$outdir/$(basename "$rpm_path")"
install -m0644 "$rpm_path" "$destination"
printf '%s\n' "$destination"
