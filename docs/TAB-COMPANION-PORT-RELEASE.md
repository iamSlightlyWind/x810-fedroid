# Fedora port updates in Tab Companion

## One rolling release for the port

Most userspace and device-integration fixes belong in `rootfs/overlay/` and
are shipped together in one cumulative, noarch `x810-fedora-port` RPM. There is
one package for the current port state, not a separate package per fix. Update
`PORT_VERSION` only for a deliberate port-version change; every successful run
on a main push also gets a unique RPM release based on its Actions run number.
Manual/reset runs reuse the last successful push identity so Tab Companion can
still resolve the bundle.

Pushing a relevant change to `main` runs `.github/workflows/x810-fedora.yml`.
The workflow:

1. builds the updater ZIP from `rootfs/overlay/` plus its port/build metadata;
2. reuses the current release's kernel or rootfs component when its input
   fingerprint and checksums still match, otherwise builds that component;
3. assembles one run-keyed release containing the updater ZIP, matched
   rootfs/kernel/boot assets, checksums, release index, and clean-install
   bundle; and
4. after successful assembly, deletes every older/staging release, leaving only
   the latest complete aggregate.

The release tag begins `x810-fedora-port-build-` because Tab Companion resolves
that asset using the successful push's Actions run ID. Its `x810-fedora-port.zip`
contains the `tab-companion-update.json` manifest with Fedora 44/aarch64/
SM-X810 target, package NEVRA, size, and SHA-256. The separate
`port-release.json` in the same release is compatibility metadata for other
consumers.

If a build fails before the new aggregate is complete, cleanup removes the
incomplete/staging releases and retains the previous GitHub `latest` release.
The kernel/rootfs packages are copied forward as assets when unchanged; they
are not rebuilt just to refresh the aggregate.

For a maintainer: add a fix to `rootfs/overlay/`, run the local contract tests,
then push to `main`. For a tablet user: open Tab Companion's **Updates** page,
update/restart Tab Companion itself if offered, then check and install the
**Linux port** update. The app validates the target, package metadata, file size,
and SHA-256 before asking DNF to install; users do not fetch the RPM manually.

The package RPM release changes each run so DNF sees an upgrade even when
`PORT_VERSION` is unchanged. Installed build metadata prevents offering that
same successful build again.

## Kernel and clean installation

Kernel and boot-image changes are intentionally not installed by the in-OS
updater. They ship in the same aggregate release for manual TWRP installation.
The clean installer selects the clean-install bundle in that release and
validates its embedded `x810-clean-install-manifest.json`; it does not consume
`port-release.json`.

When building a full rootfs/full-set by hand, pass the same explicit numeric
version as `PORT_VERSION`. A developer/debug rootfs may use `unknown`, but it
cannot be published as an updater-installable release until rebuilt with a
numeric version.
