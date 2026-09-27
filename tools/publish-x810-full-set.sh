#!/usr/bin/env bash
# Deterministically validate, assemble, and publish the combined X810 release.
set -euxo pipefail

mkdir -p k r
gh release -R "$GITHUB_REPOSITORY" download "$KREL" --dir k --clobber
gh release -R "$GITHUB_REPOSITORY" download "$RREL" --dir r --clobber
if [ ! -f k/BUILD-METADATA.txt ] || [ ! -f k/BUNDLE-SHA256SUMS ]; then
  kernel_manifest=k/manifest.json
  [ -f "$kernel_manifest" ] || kernel_manifest=k/x810-release-manifest.json
  python3 tools/x810-release-manifest.py materialize \
    "$kernel_manifest" kernel k
fi
if [ ! -f r/rootfs-manifest.txt ] || [ ! -f r/SHA256SUMS ]; then
  rootfs_manifest=r/manifest.json
  [ -f "$rootfs_manifest" ] || rootfs_manifest=r/x810-release-manifest.json
  python3 tools/x810-release-manifest.py materialize \
    "$rootfs_manifest" rootfs r
fi
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
   [[ ! "$port_package_asset" =~ ^x810-fedora-port-[A-Za-z0-9._+-]+\.noarch\.rpm$ ]]; then
  echo "REFUSING: rootfs manifest has invalid noarch x810-fedora-port metadata." >&2
  exit 1
fi
shopt -s nullglob
rootfs_archives=(r/x810-fedora-*-rootfs.tar.gz)
if [ "${#rootfs_archives[@]}" -eq 0 ] && [ -f r/rootfs.tar.gz ]; then
  rootfs_archives=(r/rootfs.tar.gz)
fi
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

rm r/port.json

# A combined release must be a matched pair: the RPM shipped next to
# the tarball has to be the one the rootfs image was built with.
# build-rootfs.sh records the installed kernel RPM in the manifest.
kernel_rpms=(k/linux-x810-*.rpm)
if [ "${#kernel_rpms[@]}" -eq 0 ] && [ -f k/kernel.rpm ]; then
  kernel_rpms=(k/kernel.rpm)
fi
[ "${#kernel_rpms[@]}" -eq 1 ] || {
  echo "REFUSING: expected exactly one linux-x810 RPM in $KREL." >&2
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

# The support updater ZIP is published by the independent port-update job.
mkdir -p update
gh release -R "$GITHUB_REPOSITORY" download "$REL" --dir update \
  --pattern update.zip --clobber

python3 - "$PORT_VERSION" update/update.zip <<'PY'
import hashlib
import json
import sys
import zipfile

version, path = sys.argv[1:]
try:
    with zipfile.ZipFile(path) as archive:
        doc = json.loads(archive.read("tab-companion-update.json"))
        if not isinstance(doc, dict):
            raise ValueError("updater metadata is not a JSON object")
        entries = doc.get("assets", [])
        if (doc.get("schema_version") != 1 or doc.get("project") != "x810-fedora"
                or not str(doc.get("version", "")).startswith(version + "+")
                or len(entries) != 1):
            raise ValueError("updater metadata does not match this X810 Fedora release")
        record = entries[0]
        if not isinstance(record, dict):
            raise ValueError("updater package metadata is malformed")
        if (record.get("package_name") != "x810-fedora-port"
                or not str(record.get("package_version", "")).startswith(version + "-")
                or not str(record.get("name", "")).endswith(".noarch.rpm")
                or record.get("target") != {"os_id": "fedora", "os_version": "44", "arch": "aarch64", "device": "SM-X810"}):
            raise ValueError("updater package target/version is not the expected Fedora 44 X810 noarch RPM")
        payload = archive.read(record["name"])
        if len(payload) != record.get("size") or hashlib.sha256(payload).hexdigest() != record.get("sha256"):
            raise ValueError("support RPM inside updater ZIP failed its size/SHA-256 check")
except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as error:
    raise SystemExit(f"REFUSING: invalid update.zip: {error}")
PY

gh release -R "$GITHUB_REPOSITORY" view "$REL" >/dev/null 2>&1 || \
  gh release -R "$GITHUB_REPOSITORY" create "$REL" --title "$REL" --latest=false \
    --notes "Matched Fedora SM-X810 install/update set: rootfs archive, kernel RPM, four boot images, Tab Companion port updater, and compact release manifest from \`$KREL\` and \`$RREL\`. The installer downloads required assets directly from this release and verifies their checksums. No TWRP, vbmeta, recovery, or Android firmware is included; use the matching TWRP port instructions."
gh release -R "$GITHUB_REPOSITORY" view "$REL" --json isDraft,isPrerelease > release-flags.json
python3 - release-flags.json <<'PY'
import json
import sys
from pathlib import Path

flags = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if flags.get("isDraft") is not False or flags.get("isPrerelease") is not False:
    raise SystemExit("REFUSING: combined latest-feed release must be published and non-prerelease.")
PY
# KREL/RREL may both resolve to the last aggregate release. In that case both
# download directories contain the same flattened asset names, so uploading
# both `*` globs causes GitHub's 422 duplicate-asset error even with --clobber.
# Publish only each component's owned files; regenerate full-set metadata below.
if [ "${kernel_rpms[0]}" != k/kernel.rpm ]; then
  cp -L "${kernel_rpms[0]}" k/kernel.rpm
fi
if [ "${rootfs_archives[0]}" != r/rootfs.tar.gz ]; then
  cp -L "${rootfs_archives[0]}" r/rootfs.tar.gz
fi

full_assets=(
  k/boot.img
  k/init_boot.img
  k/vendor_boot.img
  k/dtbo.img
  k/kernel.rpm
  r/rootfs.tar.gz
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
all_payloads=("${full_assets[@]}" update/update.zip)
manifest_asset_args=()
for asset in "${all_payloads[@]}"; do manifest_asset_args+=(--asset "$asset"); done
python3 tools/x810-release-manifest.py create \
  --kernel-dir k --rootfs-dir r \
  --source-commit "$GITHUB_SHA" --release-tag "$REL" \
  --kernel-release "$KREL" --rootfs-release "$RREL" \
  --port-version "$PORT_VERSION" --full-set-build-key "$FULL_SET_KEY" \
  --output manifest.json "${manifest_asset_args[@]}"

# A rerun must not leave obsolete metadata, vbmeta, duplicate RPMs,
# bootset ZIPs, or other superseded assets attached to the aggregate.
expected_names="$(printf '%s\n' "${full_assets[@]}" \
  update/update.zip manifest.json | sed 's#^.*/##' | sort -u)"
while IFS= read -r old_name; do
  [ -n "$old_name" ] || continue
  if ! grep -Fxq "$old_name" <<< "$expected_names"; then
    gh release -R "$GITHUB_REPOSITORY" delete-asset "$REL" "$old_name" --yes
  fi
done < <(gh release -R "$GITHUB_REPOSITORY" view "$REL" --json assets --jq '.assets[].name')

gh release -R "$GITHUB_REPOSITORY" upload "$REL" --clobber \
  "${full_assets[@]}" update/update.zip manifest.json
# Auxiliary kernel/rootfs/boot tags are not latest; reassert that
# the combined full-set release is the single updater feed even when
# rerunning an existing release.
gh release -R "$GITHUB_REPOSITORY" edit "$REL" --latest
latest_tag=$(gh api "repos/$GITHUB_REPOSITORY/releases/latest" --jq .tag_name)
[ "$latest_tag" = "$REL" ] || {
  echo "REFUSING: GitHub latest release is $latest_tag, not combined release $REL." >&2
  exit 1
}
