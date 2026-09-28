#!/usr/bin/env bash
# Build the exact SSC-backed iio-sensor-proxy stack used by both the X810
# image and the Tab Companion support RPM. Run inside Fedora 44 aarch64.
set -euo pipefail

if [ "$#" -ne 1 ]; then
	echo "usage: $0 DESTDIR" >&2
	exit 2
fi

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
dest="$(realpath -m "$1")"
if [ "$dest" = / ] || [ ! -d "$dest" ]; then
	echo "build-x810-sensor-proxy: DESTDIR must be an existing non-root directory" >&2
	exit 2
fi
for command in curl sha256sum tar meson ninja patch pkg-config python3; do
	command -v "$command" >/dev/null || {
		echo "build-x810-sensor-proxy: required build tool not found: $command" >&2
		exit 2
	}
done

lock="$repo_dir/specs/iio-sensor-proxy-libssc/sources.lock.json"
readarray -t source_values < <(python3 - "$lock" <<'PY'
import json
import re
import sys
from pathlib import Path

doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(doc, dict) or doc.get("schema_version") != 1:
    raise SystemExit("invalid sensor source lock schema")
for name in ("libssc", "iio_sensor_proxy"):
    source = doc.get(name)
    if not isinstance(source, dict):
        raise SystemExit(f"invalid source lock entry: {name}")
    values = [source.get(key) for key in ("version", "commit", "url", "sha256")]
    if not all(isinstance(value, str) and value and "\n" not in value for value in values):
        raise SystemExit(f"incomplete source lock entry: {name}")
    version, commit, url, digest = values
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version):
        raise SystemExit(f"invalid source version: {name}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit not in url:
        raise SystemExit(f"invalid immutable source commit URL: {name}")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(f"invalid source archive SHA-256: {name}")
    print(url)
    print(digest)
PY
)
if [ "${#source_values[@]}" -ne 4 ]; then
	echo "build-x810-sensor-proxy: incomplete source lock" >&2
	exit 1
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/x810-sensor-stack.XXXXXXXX")"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/libssc" "$work/iio-sensor-proxy" "$work/libssc-dest" "$work/proxy-dest" "$work/runtime"

fetch_locked_source() {
	local label="$1" url="$2" expected_sha="$3" destdir="$4"
	local archive actual
	archive="$work/${label}.tar.gz"
	if ! curl -fLsS --retry 3 --max-time 300 -o "$archive" "$url"; then
		echo "build-x810-sensor-proxy: failed to download pinned $label source" >&2
		return 1
	fi
	actual="$(sha256sum "$archive" | cut -d' ' -f1)"
	if [ "$actual" != "$expected_sha" ]; then
		echo "build-x810-sensor-proxy: SHA-256 mismatch for $label" >&2
		echo "  got:  $actual" >&2
		echo "  want: $expected_sha" >&2
		return 1
	fi
	tar xzf "$archive" -C "$destdir" --strip-components=1
}

fetch_locked_source libssc "${source_values[0]}" "${source_values[1]}" "$work/libssc"
echo ">>> Applying X810-specific libssc fixes" >&2
for patch_file in "$repo_dir"/specs/libssc-samsung/patches/*.patch; do
	[ -f "$patch_file" ] || continue
	patch --batch --fuzz=0 --forward -d "$work/libssc" -p1 < "$patch_file"
done
python3 "$repo_dir/tools/test-x810-libssc-patches.py" "$work/libssc"
echo ">>> Building pinned libssc with Fedora-native toolchain" >&2
meson setup "$work/libssc-build" "$work/libssc" -Dprefix=/usr -Db_lto=true >&2
meson compile -C "$work/libssc-build" >&2
DESTDIR="$work/libssc-dest" meson install --no-rebuild -C "$work/libssc-build" >&2

# The Fedora build container is disposable. Installing this pinned library
# into it lets Meson/pkg-config resolve its generated .pc file and runtime .so
# exactly as the existing image build did, without changing the target rootfs.
cp -a "$work/libssc-dest/." /
export PKG_CONFIG_PATH="/usr/lib64/pkgconfig:/usr/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
if [ "$(pkg-config --modversion libssc)" != "0.4.4" ]; then
	echo "build-x810-sensor-proxy: pkg-config did not resolve pinned libssc 0.4.4" >&2
	exit 1
fi

fetch_locked_source iio-sensor-proxy "${source_values[2]}" "${source_values[3]}" "$work/iio-sensor-proxy"
LC_ALL=C
export LC_ALL
for patch_file in "$repo_dir"/specs/iio-sensor-proxy-libssc/patches/*.patch; do
	patch -d "$work/iio-sensor-proxy" -p1 < "$patch_file"
done
python3 "$repo_dir/tools/test-x810-sensor-proxy-claim-race.py" "$work/iio-sensor-proxy"

echo ">>> Building pinned iio-sensor-proxy with SSC backend and X810 race fixes" >&2
meson setup "$work/iio-build" "$work/iio-sensor-proxy" \
	-Dprefix=/usr \
	-Dssc-support=enabled \
	-Dudevrulesdir=/usr/lib/udev/rules.d \
	-Dsystemdsystemunitdir=/usr/lib/systemd/system >&2
meson compile -C "$work/iio-build" >&2
meson test --no-rebuild --print-errorlogs -C "$work/iio-build" >&2
DESTDIR="$work/proxy-dest" meson install --no-rebuild -C "$work/iio-build" >&2

# Keep only the runtime libssc ABI and ssccli (used by the sensor recovery
# unit); headers, pkg-config data, mock servers, and installed-test fixtures
# are build-time/development content. The same trimmed runtime tree is used
# for both the fresh image and updater RPM.
mapfile -t libssc_runtime < <(find "$work/libssc-dest/usr/lib64" -maxdepth 1 \
	-type f -name 'libssc.so.*' -print | LC_ALL=C sort)
if [ "${#libssc_runtime[@]}" -ne 1 ] || \
   [ "$(basename "${libssc_runtime[0]:-}")" != libssc.so.2 ]; then
	echo "build-x810-sensor-proxy: expected the pinned libssc.so.2 runtime ABI" >&2
	exit 1
fi
install -Dm0755 "${libssc_runtime[0]}" "$work/runtime/usr/lib64/$(basename "${libssc_runtime[0]}")"
install -Dm0755 "$work/libssc-dest/usr/bin/ssccli" "$work/runtime/usr/bin/ssccli"
cp -a "$work/proxy-dest/." "$work/runtime/"
cp -a "$work/runtime/." "$dest/"
for required in \
	usr/libexec/iio-sensor-proxy \
	usr/bin/monitor-sensor \
	usr/bin/ssccli \
	usr/lib/systemd/system/iio-sensor-proxy.service \
	usr/lib/udev/rules.d/80-iio-sensor-proxy.rules \
	usr/share/dbus-1/system.d/net.hadess.SensorProxy.conf \
	usr/share/polkit-1/actions/net.hadess.SensorProxy.policy; do
	if [ ! -f "$dest/$required" ]; then
		echo "build-x810-sensor-proxy: expected runtime output missing: /$required" >&2
		exit 1
	fi
done
if ! find "$dest/usr/lib64" -maxdepth 1 -type f -name 'libssc.so.*' -print -quit | grep -q .; then
	echo "build-x810-sensor-proxy: libssc shared library missing from output" >&2
	exit 1
fi
echo "Staged pinned SSC sensor stack into $dest" >&2
