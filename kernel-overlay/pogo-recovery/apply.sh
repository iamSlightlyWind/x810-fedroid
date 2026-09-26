#!/usr/bin/env bash
set -euo pipefail

if [ "$#" != 1 ]; then
	echo "usage: $0 <ubuntu-galaxy-tab-s9-ultra-repo>" >&2
	exit 2
fi

repo=$(realpath "$1")
patch_dir=$(cd "$(dirname "$0")/patches" && pwd)

git -C "$repo" apply --check \
	"$patch_dir/0001-add-explicit-samsung-pogo-recovery.patch"
git -C "$repo" apply \
	"$patch_dir/0001-add-explicit-samsung-pogo-recovery.patch"
echo "Added privileged /sys/.../recover trigger; source-only, no device touched."
