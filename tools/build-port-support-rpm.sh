#!/usr/bin/env bash
# Build the updater-installable Fedora support RPM from the X810 rootfs overlay.
# The image's kernel, boot chain, firmware, and Fedora base packages are excluded.
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
if ! command -v rpmbuild >/dev/null || ! command -v rpm >/dev/null; then
	echo "build-port-support-rpm: rpmbuild and rpm are required (install rpm-build)" >&2
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
install -Dm0644 "$rootfs/usr/share/tab-companion/port.json" \
	"$stage/usr/share/tab-companion/port.json"
if [ -f "$build_info" ]; then
	install -Dm0644 "$build_info" "$stage/usr/share/tab-companion/port-build.json"
fi
tar -C "$stage" -czf "$top/SOURCES/port-overlay.tar.gz" .
(cd "$stage" && find . \( -type f -o -type l \) -printf '/%P\n' | LC_ALL=C sort) \
	> "$top/SOURCES/port-overlay.filelist"
cp "$repo_dir/specs/x810-fedora-port.spec" "$top/SPECS/"

rpmbuild --define "_topdir $top" --define "port_version $version" \
	--define "port_release $rpm_release" \
	-bb "$top/SPECS/x810-fedora-port.spec" >&2

mapfile -t built < <(find "$top/RPMS/noarch" -maxdepth 1 -type f \
	-name 'x810-fedora-port-*.noarch.rpm' -print)
if [ "${#built[@]}" -ne 1 ]; then
	echo "build-port-support-rpm: expected exactly one noarch RPM" >&2
	exit 1
fi
rpm_path="${built[0]}"
rpm_name="$(rpm -qp --qf '%{NAME}' "$rpm_path")"
rpm_version="$(rpm -qp --qf '%{VERSION}' "$rpm_path")"
rpm_release="$(rpm -qp --qf '%{RELEASE}' "$rpm_path")"
rpm_arch="$(rpm -qp --qf '%{ARCH}' "$rpm_path")"
if [ "$rpm_name" != x810-fedora-port ] || [ "$rpm_version" != "$version" ] || [ "$rpm_arch" != noarch ]; then
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
if grep -Eq '^/(boot|boot/|lib/modules/|usr/lib/modules/|usr/lib/firmware/|lib/firmware/)' "$work/package-files"; then
	echo "build-port-support-rpm: refusing package that contains boot/kernel/firmware files" >&2
	exit 1
fi

destination="$outdir/$(basename "$rpm_path")"
install -m0644 "$rpm_path" "$destination"
printf '%s\n' "$destination"
