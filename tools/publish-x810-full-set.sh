#!/usr/bin/env bash
# Deterministically validate, assemble, and publish the combined X810 release.
set -euxo pipefail

mkdir -p k r
gh release -R "$GITHUB_REPOSITORY" download "$KREL" --dir k --clobber
gh release -R "$GITHUB_REPOSITORY" download "$RREL" --dir r --clobber
(cd k && sha256sum -c BUNDLE-SHA256SUMS && sha256sum -c RPM-SHA256SUMS)
(cd r && sha256sum -c SHA256SUMS)
if [ -f k/KERNEL-BUILD-KEY.txt ]; then
  [ "$(cat k/KERNEL-BUILD-KEY.txt)" = "$KERNEL_BUILD_KEY" ] || {
    echo "REFUSING: selected kernel release has a different build key." >&2; exit 1;
  }
fi
printf '%s\n' "$KERNEL_BUILD_KEY" > k/KERNEL-BUILD-KEY.txt
ROOTFS_BUILD_KEY="$(cat r/ROOTFS-BUILD-KEY.txt)"
FULL_SET_KEY="$(python3 tools/x810-build-fingerprint.py full-set \
  --ref "$GITHUB_SHA" --port-version "$PORT_VERSION" \
  --kernel-build-key "$KERNEL_BUILD_KEY" --rootfs-build-key "$ROOTFS_BUILD_KEY")"
[ "$FULL_SET_KEY" = "$EXPECTED_FULL_SET_KEY" ] || {
  echo "REFUSING: full-set fingerprint changed after orchestration planning." >&2; exit 1;
}
printf '%s\n' "$FULL_SET_KEY" > FULL-SET-BUILD-KEY.txt

# Release tags identify build artifacts, not updater versions.
# Require the operator's explicit version and prove it matches both
# the manifest and the archived filesystem before publishing.
if [[ ! "$PORT_VERSION" =~ ^(0|[1-9][0-9]{0,19})\.(0|[1-9][0-9]{0,19})\.(0|[1-9][0-9]{0,19})$ ]]; then
  echo "REFUSING: port_version must be an explicit numeric MAJOR.MINOR.PATCH version." >&2
  exit 1
fi
mapfile -t recorded_versions < <(grep '^port_version=' r/rootfs-manifest.txt || true)
[ "${#recorded_versions[@]}" -eq 1 ] || {
  echo "REFUSING: rootfs manifest must contain exactly one port_version." >&2
  exit 1
}
manifest_version="${recorded_versions[0]#port_version=}"
[ "$manifest_version" = "$PORT_VERSION" ] || {
  echo "REFUSING: requested port version $PORT_VERSION does not match rootfs manifest $manifest_version." >&2
  exit 1
}
manifest_value() {
  local key="$1"
  local -a lines
  mapfile -t lines < <(grep "^${key}=" r/rootfs-manifest.txt || true)
  [ "${#lines[@]}" -eq 1 ] || return 1
  printf '%s' "${lines[0]#*=}"
}
port_package_name="$(manifest_value port_package_name)" || {
  echo "REFUSING: rootfs manifest is missing package metadata." >&2; exit 1;
}
port_package_version="$(manifest_value port_package_version)" || {
  echo "REFUSING: rootfs manifest is missing package metadata." >&2; exit 1;
}
port_package_arch="$(manifest_value port_package_arch)" || {
  echo "REFUSING: rootfs manifest is missing package metadata." >&2; exit 1;
}
port_package_asset="$(manifest_value port_package_asset)" || {
  echo "REFUSING: rootfs manifest is missing package metadata." >&2; exit 1;
}
if [ "$port_package_name" != x810-fedora-port ] || [ "$port_package_arch" != noarch ] || \
   [[ "$port_package_version" != "$PORT_VERSION"-* ]] || \
   [[ ! "$port_package_asset" =~ ^x810-fedora-port-[A-Za-z0-9._+-]+\.noarch\.rpm$ ]] || \
   [ ! -f "r/$port_package_asset" ]; then
  echo "REFUSING: rootfs release lacks a matching noarch x810-fedora-port RPM." >&2
  exit 1
fi
shopt -s nullglob
rootfs_archives=(r/gts9wifi-fedora-*-rootfs.tar.gz)
[ "${#rootfs_archives[@]}" -eq 1 ] || {
  echo "REFUSING: expected exactly one rootfs tarball in $RREL." >&2
  exit 1
}
python3 tools/verify-x810-rootfs-archive.py \
  "${rootfs_archives[0]}" --manifest r/rootfs-manifest.txt
tar -xOzf "${rootfs_archives[0]}" ./usr/share/tab-companion/port.json > r/port.json
python3 - "$PORT_VERSION" r/port.json <<'PY'
import json
import re
import sys
from pathlib import Path

expected_version, metadata_path = sys.argv[1:]
document = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
expected_fields = {
    "schema_version": 1,
    "port_id": "x810-fedora",
    "device_id": "SM-X810",
    "os_id": "fedora",
    "arch": "aarch64",
}
if not isinstance(document, dict) or any(document.get(key) != value for key, value in expected_fields.items()):
    raise SystemExit("REFUSING: rootfs port.json has the wrong schema or target.")
if document.get("version") != expected_version:
    raise SystemExit("REFUSING: rootfs port.json version does not match the requested port version.")
if not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})\.(?:0|[1-9][0-9]{0,19})", expected_version):
    raise SystemExit("REFUSING: unknown or malformed port versions cannot be published.")
PY

# The updater consumes one schema-1 index from the combined release.
# This local generator mirrors Tab Companion's index writer and also
# checks the RPM's actual NEVRA before hashing it.
python3 tools/make-port-release-index.py \
  --manifest r/rootfs-manifest.txt \
  --rpm "r/$port_package_asset" \
  --port-json r/port.json \
  --repository "$GITHUB_REPOSITORY" \
  --tag "$REL" \
  --output port-release.json
rm r/port.json

# A combined release must be a matched pair: the RPM shipped next to
# the tarball has to be the one the rootfs image was built with.
# build-rootfs.sh records the installed kernel RPM in the manifest.
kernel_rpms=(k/linux-gts9wifi-*.rpm)
[ "${#kernel_rpms[@]}" -eq 1 ] || {
  echo "REFUSING: expected exactly one linux-gts9wifi RPM in $KREL." >&2
  exit 1
}
kernel_nevra=$(rpm -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "${kernel_rpms[0]}")
grep -Fxq "$kernel_nevra" r/rootfs-manifest.txt || {
  echo "REFUSING: $kernel_nevra does not exactly match a kernel package in $RREL's manifest." >&2
  exit 1
}
python3 tools/verify-x810-build-match.py \
  --rootfs-manifest r/rootfs-manifest.txt \
  --kernel-metadata k/BUILD-METADATA.txt \
  --kernel-rpm "${kernel_rpms[0]}"

# This is a separate from-stock installer payload, not the Tab
# Companion updater index.  The assembler verifies source release
# checksums again and embeds a deterministic archive-root manifest.
clean_install_asset="x810-fedora-sm-x810-${REL}-clean-install.tar.gz"
python3 tools/build-x810-clean-install-bundle.py \
  --kernel-dir k \
  --rootfs-dir r \
  --output "$clean_install_asset" \
  --bundle-version "$REL" \
  --kernel-release "$KREL" \
  --rootfs-release "$RREL"

gh release -R "$GITHUB_REPOSITORY" view "$REL" >/dev/null 2>&1 || \
  gh release -R "$GITHUB_REPOSITORY" create "$REL" --title "$REL" --latest=false \
    --notes "Matched Fedora SM-X810 update set: kernel RPM, Android boot bundle, X810-only TWRP boot-set ZIP, and rootfs tarball from \`$KREL\` and \`$RREL\`.

The \`x810-fedora-sm-x810-${REL}-clean-install.tar.gz\` asset is the separate clean-install bundle; inspect its \`x810-clean-install-manifest.json\` and follow INSTALL.md. It contains no vbmeta or recovery image. The legacy TWRP ZIP rewrites only boot, init_boot, vendor_boot, and dtbo; it preserves vbmeta, recovery, userdata, GPT, and firmware.

Checksums: SHA256SUMS covers the rootfs tarball, BUNDLE-SHA256SUMS covers the boot images."
gh release -R "$GITHUB_REPOSITORY" view "$REL" --json isDraft,isPrerelease > release-flags.json
python3 - release-flags.json <<'PY'
import json
import sys
from pathlib import Path

flags = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if flags.get("isDraft") is not False or flags.get("isPrerelease") is not False:
    raise SystemExit("REFUSING: combined latest-feed release must be published and non-prerelease.")
PY
# If this tag already had a feed index, remove it before replacing
# its referenced RPM. On a failed rerun, clients see no update rather
# than a stale index whose checksum no longer matches the asset.
if [ "$(gh release -R "$GITHUB_REPOSITORY" view "$REL" --json assets \
    --jq '[.assets[].name] | index("port-release.json") != null')" = true ]; then
  gh release -R "$GITHUB_REPOSITORY" delete-asset "$REL" port-release.json --yes
fi
# KREL/RREL may both resolve to the last aggregate release. In that case both
# download directories contain the same flattened asset names, so uploading
# both `*` globs causes GitHub's 422 duplicate-asset error even with --clobber.
# Publish only each component's owned files; regenerate full-set metadata below.
full_assets=(
  k/BUILD-METADATA.txt
  k/BUNDLE-SHA256SUMS
  k/KERNEL-BUILD-KEY.txt
  k/RPM-SHA256SUMS
  k/*.img
  k/linux-gts9wifi-*.rpm
  k/gts9wifi-fedora-*.zip
  r/ROOTFS-BUILD-KEY.txt
  r/SHA256SUMS
  r/rootfs-manifest.txt
  r/gts9wifi-fedora-*-rootfs.tar.gz
  r/x810-fedora-port-*.rpm
)
declare -A seen_assets=()
for asset in "${full_assets[@]}"; do
  [ -f "$asset" ] || { echo "REFUSING: required aggregate asset is missing: $asset" >&2; exit 1; }
  name="$(basename "$asset")"
  [ -z "${seen_assets[$name]+x}" ] || {
    echo "REFUSING: duplicate basename in aggregate inputs: $name" >&2; exit 1;
  }
  seen_assets[$name]=1
done
gh release -R "$GITHUB_REPOSITORY" upload "$REL" --clobber "${full_assets[@]}"
gh release -R "$GITHUB_REPOSITORY" upload "$REL" --clobber port-release.json FULL-SET-BUILD-KEY.txt
gh release -R "$GITHUB_REPOSITORY" upload "$REL" --clobber "$clean_install_asset"
# Auxiliary kernel/rootfs/boot tags are not latest; reassert that
# the combined full-set release is the single updater feed even when
# rerunning an existing release.
gh release -R "$GITHUB_REPOSITORY" edit "$REL" --latest
latest_tag=$(gh api "repos/$GITHUB_REPOSITORY/releases/latest" --jq .tag_name)
[ "$latest_tag" = "$REL" ] || {
  echo "REFUSING: GitHub latest release is $latest_tag, not combined release $REL." >&2
  exit 1
}
