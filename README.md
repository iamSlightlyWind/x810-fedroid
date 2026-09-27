# Fedora on Samsung Galaxy Tab S9+ Wi-Fi (SM-X810)

An experimental native Fedora 44 port for `gts9pwifi` / Qualcomm SM8550.
The current tablet has booted Fedora 44 on internal UFS (`linuxroot`) with
Linux `7.2.0-gts9wifi`; this is not yet a clean-room release or a validated
installer. See the cross-project status and constraints in
[`linux-ports-docs`](https://github.com/iamSlightlyWind/linux-ports-docs).

> **Origin and AI disclaimer:** This port uses the X710 Fedora project
> [`nacht20-de/gts9wifi-fedora-linux`](https://github.com/nacht20-de/gts9wifi-fedora-linux)
> as its build/userspace prior art
> plus exact X810 CYG1 Samsung sources and X810 research recorded in `docs/`.
> Most of the X810 porting and repository work was produced with AI
> assistance. It is experimental, may be wrong or incomplete, and is not an
> official Samsung/Fedora release. Review and physically validate before use.

## Scope

This repository owns X810-specific kernel, device tree, Fedora rootfs,
firmware staging and TWRP boot tooling. Tab Companion source, update UI, and
its device adapters are maintained separately in
[`tab-companion`](https://github.com/iamSlightlyWind/tab-companion). Shared
requirements, concise status and app/port architecture are in
[`linux-ports-docs`](https://github.com/iamSlightlyWind/linux-ports-docs).

`docs/x810-research/` preserves the initial device investigation and original
project brief. The X710 scripts and files are a starting point, not proof that
an X810 image can be rebuilt or flashed safely. The root `INSTALL.md` states
that boundary; the inherited X710 guide is retained under
`docs/reference/X710-INSTALL.md` only.

## Installation status

For the Linux-PC install flow, read [`INSTALL.md`](INSTALL.md) and run
`python3 tools/x810-install` for the guided wizard. It validates Android
unlock/root status, TWRP, and the aggregate release manifest plus install
assets; captures GPT metadata and the current four boot images; asks for
account and partition sizes; and places
typed barriers before writes. For a stock GPT, it changes only userdata and
linuxroot entries, saves a credential-free resume checkpoint, and stops so the
owner can manually reboot into TWRP and rerun `--resume-from`. Android userdata
format remains a manual TWRP UI action. Rootfs and the four boot images are
installed only after resume validation. An explicit TWRP-only command can
restore the checksummed four-image backup without touching `vbmeta` or
recovery. The flow has host/mock tests only and is **not physically validated
on the tablet**; do not treat it as a proven end-user installer. The release
check reports the compact release manifest and direct install assets.
The in-OS port updater uses the support-package ZIP in the same rolling
aggregate release as the Fedora rootfs and boot files. Never use the legacy
whole-userdata formatter for dual boot.

## Port update identity

Rootfs builds include `/usr/share/tab-companion/port.json`. Local/debug builds
default to `version: "unknown"`; a release must supply an explicit numeric
`PORT_VERSION` (`MAJOR.MINOR.PATCH`), separate from the kernel/rootfs/release
tags. The rootfs workflow records that value in both the image and its manifest
and, only for a known version, builds/installs a noarch `x810-fedora-port` RPM
from the port-owned overlay. The full-set workflow requires the same version
and verifies the image/RPM contract before publishing; unknown or mismatched
images cannot become combined releases. The rootfs build downloads its kernel
RPM from a named release of this X810 repository, and full-set assembly checks
the exact RPM bytes and firmware digest against the kernel build metadata, not
just matching version strings. The combined `x810-fedora.yml` workflow has a
separate support-package job that publishes the overlay RPM ZIP for Tab
Companion's port updater. Once the aggregate is complete, exactly one GitHub
release remains: the latest run's release, containing the updater ZIP, matched
rootfs/kernel assets, four individual boot images, and one compact release manifest.
The manifest records the source commit, per-component build fingerprints, and
payload checksums; it replaces the separate checksum/key text assets and the
legacy `port-release.json`. Unchanged kernel and rootfs components are reused
from the previous aggregate. The installer downloads required files
individually from the release and validates their checksums against the
manifest. The redundant bootset ZIP, standalone support RPM, and large
clean-install archive are omitted. See
[`docs/TAB-COMPANION-PORT-RELEASE.md`](docs/TAB-COMPANION-PORT-RELEASE.md).
