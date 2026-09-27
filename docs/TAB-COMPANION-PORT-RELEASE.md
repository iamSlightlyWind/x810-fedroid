# Fedora port updates in Tab Companion

## Ordinary Fedora/device-support fixes

Most userspace and device-integration fixes belong in `rootfs/overlay/` and
are shipped together in one cumulative, noarch `x810-fedora-port` RPM. There
is one package for the current port state, not a separate package for each
change. Update `PORT_VERSION` only when making a deliberate port-version
change; every successful `main` build also gets a unique increasing RPM
release based on the GitHub Actions run number.

Pushing a change to `main` runs `.github/workflows/x810-fedora.yml`. Its
`build_port_update` job is separate from the kernel/rootfs image jobs and:

1. stages `port.json` and a build identity from that exact commit/run;
2. packages only `rootfs/overlay/` plus those two metadata files into the
   `x810-fedora-port` RPM (no Fedora base, kernel, boot images, or firmware);
3. creates the same schema-1 `tab-companion-update.json` manifest used by the
   Tab Companion self-updater, including Fedora 44/aarch64/SM-X810 target,
   package NEVRA, size, and SHA-256;
4. publishes `x810-fedora-port.zip` to a run-keyed public GitHub release;
5. after the full combined workflow succeeds, removes only older releases with
   the dedicated `x810-fedora-port-build-` tag prefix. A delayed older run
   cannot delete a newer release, and a failed image build leaves the prior
   updater release available.

For a maintainer, that means: add the fix to `rootfs/overlay/`, run the local
contract tests, then push to `main`. For a tablet user, open Tab Companion's
**Updates** page, update/restart Tab Companion itself if offered, then check
and install the **Linux port** update. The app handles download, validation,
confirmation, and DNF installation; the user does not fetch an RPM manually.

`/usr/share/tab-companion/port.json` configures the app to query
`x810-fedora.yml`, use the `x810-fedora-port` release asset, and compare the
installed run in `/usr/share/tab-companion/port-build.json`. The ZIP is
downloaded without login; Tab Companion validates the workflow run identity,
target, package metadata, file size and SHA-256 before asking DNF to install.
The package's RPM release changes on every run, so DNF sees a real upgrade even
when `PORT_VERSION` is unchanged. After installation, its build metadata
prevents offering that same successful build again.

Kernel and boot-image changes are intentionally not included in this updater.
They are handled by separate jobs in the same image pipeline and are not
written by the in-OS updater.

## Full installer releases are a separate channel

The clean-install/bootstrap path continues to use the combined full-set GitHub
release selected by `releases/latest`. That release still contains the matched
rootfs/kernel/boot set, clean-install bundle, and `port-release.json` legacy
compatibility index. `x810-install` selects the clean-install bundle and
validates its embedded `x810-clean-install-manifest.json`; it does not consume
`port-release.json`. Current Tab Companion Fedora updates use the separate
per-build support-package release channel published by the `build_port_update`
job in `x810-fedora.yml`. Those releases use `--latest=false` and a distinct
tag prefix; they do not replace or prune the clean-install `latest` release.
Existing full-set releases remain available for reinstall/recovery.

When building a full rootfs/full-set by hand, pass the same explicit numeric
version as the repository's `PORT_VERSION` file. A developer/debug rootfs may
still use `unknown`; such an image cannot publish an updater-installable
full-set index until its rootfs and support RPM are rebuilt with a numeric
version.
