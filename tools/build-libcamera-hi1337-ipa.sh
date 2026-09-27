#!/usr/bin/env bash
# Build Fedora 44's simple libcamera IPA with the X810 HI1337 helper.
# Run in a native Fedora 44 aarch64 build environment; the resulting plugin
# must match the target's exact libcamera build and is intentionally unsigned
# so libcamera loads it in its normal isolated soft-IPA worker.
set -euo pipefail

if [ "$#" -ne 1 ]; then
	echo "usage: $0 STAGING_ROOT" >&2
	exit 2
fi

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
stage="$(realpath -m "$1")"
lock="$repo_dir/specs/libcamera-hi1337/fedora-44-libcamera.lock.json"
helper_patch="$repo_dir/specs/libcamera-hi1337/patches/add-hi1337-gts9u-camera-sensor-helper.patch"

for command in curl sha256sum rpm2cpio cpio tar patch meson ninja gcc c++; do
	command -v "$command" >/dev/null || {
		echo "build-libcamera-hi1337-ipa: required build tool not found: $command" >&2
		exit 2
	}
done

readarray -t lock_values < <(python3 - "$lock" <<'PY'
import json
import sys
from pathlib import Path

lock = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key in ("name", "version", "release", "srpm_url", "srpm_sha256", "source_tarball", "fedora_patch"):
    value = lock.get(key)
    if not isinstance(value, str) or not value or "\n" in value:
        raise SystemExit(f"invalid lock field: {key}")
    print(value)
PY
)
if [ "${#lock_values[@]}" -ne 7 ]; then
	echo "build-libcamera-hi1337-ipa: incomplete Fedora source lock" >&2
	exit 1
fi
name="${lock_values[0]}"
version="${lock_values[1]}"
release="${lock_values[2]}"
srpm_url="${lock_values[3]}"
srpm_sha256="${lock_values[4]}"
source_tarball="${lock_values[5]}"
fedora_patch="${lock_values[6]}"

work="$(mktemp -d "${TMPDIR:-/tmp}/x810-libcamera-hi1337.XXXXXXXX")"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/srpm" "$work/source" "$stage/usr/lib64/libcamera/ipa-x810"
srpm="$work/${name}-${version}-${release}.src.rpm"
curl -fLsS --retry 3 --max-time 300 -o "$srpm" "$srpm_url"
printf '%s  %s\n' "$srpm_sha256" "$srpm" | sha256sum --check --status || {
	echo "build-libcamera-hi1337-ipa: Fedora source RPM hash mismatch" >&2
	exit 1
}

(cd "$work/srpm" && rpm2cpio "$srpm" | cpio --quiet -idm)
test -f "$work/srpm/$source_tarball"
test -f "$work/srpm/$fedora_patch"
tar -xjf "$work/srpm/$source_tarball" -C "$work/source"
source_dir="$work/source/${name}-v${version}"
test -d "$source_dir"
patch -d "$source_dir" -p1 < "$work/srpm/$fedora_patch"
patch -d "$source_dir" -p1 < "$helper_patch"

# Match Fedora's compiler workaround and package configuration. Only the
# simple pipeline/IPA is enabled; no Raspberry Pi/desktop integration is
# needed on the X810. Meson installs the module into staging with build RPATHs
# stripped. Do not ship the generated .sign file: it was signed with this
# build's private key, not Fedora's embedded public key, and is intentionally
# untrusted so libcamera uses soft_ipa_proxy isolation.
export CXXFLAGS="${CXXFLAGS:-} -Wno-deprecated-declarations --param=max-devirt-targets=1"
build_dir="$work/build"
meson setup "$build_dir" "$source_dir" \
	--prefix=/usr --libdir=lib64 --sysconfdir=/etc \
	-Dpipelines=simple -Dipas=simple \
	-Dv4l2=enabled -Dlc-compliance=disabled -Dtest=false \
	-Ddocumentation=disabled -Drpi-awb-nn=disabled \
	-Dqcam=disabled -Dgstreamer=disabled -Dpycamera=disabled
# Build the full minimal pipeline selection so meson's install stage also has
# the shipped soft_ipa_proxy worker available.
ninja -C "$build_dir"
meson install --no-rebuild --destdir="$work/install" -C "$build_dir"

plugin="$work/install/usr/lib64/libcamera/ipa/ipa_soft_simple.so"
if [ ! -f "$plugin" ] || [ -L "$plugin" ]; then
	echo "build-libcamera-hi1337-ipa: expected installed plugin" >&2
	exit 1
fi
if ! grep -aFq 'hi1337-gts9u' "$plugin"; then
	echo "build-libcamera-hi1337-ipa: output does not contain the X810 sensor helper" >&2
	exit 1
fi
install -m0755 "$plugin" "$stage/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so"
install -Dm0644 "$source_dir/LICENSES/LGPL-2.1-or-later.txt" \
	"$stage/usr/share/licenses/x810-fedora-port/libcamera/LGPL-2.1-or-later.txt"
install -Dm0644 "$source_dir/LICENSES/BSD-2-Clause.txt" \
	"$stage/usr/share/licenses/x810-fedora-port/libcamera/BSD-2-Clause.txt"
if [ -e "$stage/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so.sign" ]; then
	echo "build-libcamera-hi1337-ipa: refusing to stage a non-Fedora IPA signature" >&2
	exit 1
fi
printf 'Staged X810 libcamera IPA: %s\n' "$stage/usr/lib64/libcamera/ipa-x810/ipa_soft_simple.so"
