#!/usr/bin/env bash
# Stage/validate the owner-authorized SM-X810 CYG1 VPU firmware into ignored
# local build assets. Reproducible CI uses the SHA-256-pinned repo payload.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="${1:-${GTS9_VPU_MBN:-}}"
dst="$repo_dir/local-assets/firmware-overrides/usr/lib/firmware/qcom/vpu/vpu30_4v.mbn"
expected="c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba"

if [[ -z "$src" || ! -f "$src" ]]; then
    echo "usage: GTS9_VPU_MBN=/path/to/your/extracted/vpu30_4v.mbn $0" >&2
    exit 2
fi

actual="$(sha256sum "$src" | awk '{print $1}')"
if [[ "$actual" != "$expected" ]]; then
    echo "Refusing to stage a non-X810-CYG1 blob." >&2
    echo "  got:      $actual" >&2
    echo "  expected: $expected" >&2
    exit 1
fi

install -D -m0644 "$src" "$dst"
echo "Staged verified owner-supplied X810 CYG1 VPU firmware in ignored local-assets/."
echo "SHA-256: $actual"
