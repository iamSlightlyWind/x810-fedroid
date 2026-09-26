#!/usr/bin/env bash
# Apply the X810 ANA38407 60/120-Hz source overlay to an unpacked Linux v7.2 tree.
set -euo pipefail
[ "$#" = 1 ] || { echo "usage: $0 <linux-v7.2-tree>" >&2; exit 2; }
tree=$(realpath "$1")
overlay=$(cd "$(dirname "$0")" && pwd)

# Install the board-specific, source-complete X810 ANA38407 panel driver.
install -D -m 0644 "$overlay/drivers/gpu/drm/panel/panel-samsung-ana38407.c" \
    "$tree/drivers/gpu/drm/panel/panel-samsung-ana38407.c"

# Add the optional mode notification to DRM panel helpers. This can be run
# once on a pristine v7.2 tree; skip when both edits are already present.
if ! grep -q 'panel_bridge_mode_set' "$tree/drivers/gpu/drm/bridge/panel.c" || \
   ! grep -q 'void (\*mode_set)(struct drm_panel \*panel' "$tree/include/drm/drm_panel.h"; then
    git -C "$tree" apply --check \
        "$overlay/patches/0001-drm-panel-mode-set-notifier.patch"
    git -C "$tree" apply \
        "$overlay/patches/0001-drm-panel-mode-set-notifier.patch"
fi
