# Install Fedora on the Galaxy Tab S9+ Wi-Fi (SM-X810)

## Quick start (Linux PC)

Clone this repository, connect the tablet by USB, then run:

```sh
python3 tools/x810-install
```

In a terminal, that starts the guided installer. It downloads the rootfs,
matching kernel RPM, and four boot images individually from the latest
aggregate release, then verifies them using `manifest.json`.
There is no separate multi-gigabyte clean-install archive. To install entirely
offline after caching a complete release, add `--no-update`. If several ADB
targets are connected, select the tablet with `--serial SERIAL`.

Before downloading the large rootfs, a fresh install checks that exactly one
usable Android ADB target is connected and that model, bootloader-unlocked, and
root checks pass. A resumed install similarly requires the tablet already be
in TWRP before it downloads. If these checks fail, fix the PC/ADB/Android state
first and rerun; no release payload is downloaded and no tablet is changed.

The host needs Python 3, `adb`, `openssl`, and `sha256sum`. Install the tools
with your distribution's package manager, for example:

```sh
# Fedora
sudo dnf install android-tools openssl coreutils
# Debian / Ubuntu
sudo apt install adb openssl coreutils
# Arch
sudo pacman -S android-tools openssl coreutils
```

Before running the guided install, boot Android, enable USB debugging, approve
the PC's ADB key, and ensure Android reports an unlocked bootloader and working
`su` root. The wizard checks the model, unlock/root properties, recovery state,
partition geometry, and required TWRP commands. Grant the ADB `su` prompt if
Magisk asks. The wizard never reboots or enters TWRP for you; follow its prompt
and enter TWRP manually. Do not proceed if any identity or geometry check
fails.

## Guided install

The wizard asks for full name, Linux username, hostname, and a hidden password.
It asks independently for the Android `userdata` and Fedora `linuxroot` sizes
in GiB or percent. Any unused portion stays unpartitioned. Fedora size must be
at least the minimum calculated from the verified rootfs archive.

The process is staged so a successful GPT write does not leave the script
assuming TWRP has refreshed its partition map:

1. The installer verifies the bundle, Android model/unlock/root, TWRP identity,
   measured X810 UFS/GPT geometry, and required recovery utilities.
2. It captures GPT metadata and the current `boot`, `init_boot`, `vendor_boot`,
   and `dtbo` images to a new private directory on the PC. This is **not** a
   data backup. It does not capture or write `recovery`, `vbmeta`, EFS, modem,
   `persist`, radio calibration, or other device-unique data.
3. It prints the planned sizes. On stock GPT, the first typed confirmation
   authorizes erasing Android userdata; a second typed confirmation authorizes
   changing only GPT entries 34 and 35. It reads back and verifies the new
   extents, saves a credential-free checkpoint, then waits in the same process
   while you manually reboot into TWRP. The installer never reboots the tablet.
   If the PC-side process is interrupted, rerun the same command with the same
   verified release cached; it detects and validates the pending checkpoint.

5. On a fresh stock split, manually use TWRP **Wipe → Format Data** and type
   `yes` when the resumed wizard asks. This erases Android apps, settings, and
   personal data so Android can initialize its resized userdata partition.
   The installer never issues a TWRP `format data` command; it checks the new
   partition size and filesystem signature before proceeding. On an already
   split device, it preserves `userdata` and skips this step.
6. After a further typed confirmation, the installer formats only
   `/dev/block/by-name/linuxroot` as ext4, streams and extracts the Fedora
   rootfs, stages verified copies of the captured Android and Fedora four-image
   boot sets under `/var/lib/x810-boot-sets/` for the system switchers,
   provisions the requested account, and writes only the four Fedora boot
   images. Staged files and partition writes are read-back verified. The
   wizard stops without rebooting.

### Install Tab Companion after the first Fedora boot

The Fedora rootfs includes X810 support; the release's
`update.zip` is for Tab Companion's later support updates. The
installer does **not** include the separate Tab Companion application package.
After the first Fedora boot, install the current Fedora RPM from the Tab Companion
repository. With GitHub CLI installed:

```sh
tag=$(gh release list --repo iamSlightlyWind/tab-companion --limit 1 \
  --json tagName --jq '.[0].tagName')
mkdir -p "$HOME/Downloads/tab-companion"
gh release download "$tag" --repo iamSlightlyWind/tab-companion \
  --pattern '*.rpm' --dir "$HOME/Downloads/tab-companion"
sudo dnf install "$HOME"/Downloads/tab-companion/*.rpm
```

Once installed, Tab Companion's **Updates** page has separate checks for the
app itself and the Fedora port. Update the app first if needed, then check and
install the **Linux port** update. This first application install is not yet
automated by `x810-install`.

The checkpoint binds the model/codename, ADB serial, TWRP version, generated
install-manifest hash, exact partition extents, and local backup checksums. It
contains no password or password hash. A changed layout, release assets, TWRP version, boot set,
or backup causes resume to stop.

### Recovery

If needed, restore the four backed-up boot partitions from TWRP with:

```sh
python3 tools/x810-install restore-boot-set \
  --backup-dir ./x810-install-backup-TIMESTAMP --serial ADB_SERIAL
```

This validates the backup manifest sidecar, every image hash and size, TWRP/X810
identity, and live partition sizes. It requires typing
`RESTORE FOUR SM-X810 BOOT IMAGES`, writes and verifies only `boot`,
`init_boot`, `vendor_boot`, and `dtbo`, and does not reboot. It does not restore
GPT or user data. If any write/read-back fails, remain in TWRP and keep the PC
backup; do not reboot into a partially written boot set.

## Validation status and limitations

The guided flow, release-asset verifier, split arithmetic, mocked GPT path,
account provisioning, and boot-image write/restore paths have host-side tests.
The repository owner reports a successful fresh installation on a physical
SM-X810, including Fedora's first GNOME/GDM boot. The installer has not yet
been independently validated across repeated installs or failure recovery.
Keep the verified PC-side backups until Fedora and Android have both been
boot-tested.

The installer downloads and verifies individual files from the single
aggregate release. To build or publish, push relevant changes to `main` or
manually run **X810 Fedora build and release** (`.github/workflows/x810-fedora.yml`)
in GitHub Actions. The orchestrator fingerprints the kernel/boot, rootfs, and
combined release inputs independently; it reuses a prior successful component
only when its fingerprint and checksums match. Otherwise it builds just the
changed component(s), then assembles and publishes a new aggregate. On a normal
push, the aggregate is tagged with that push's run identity and contains the
updater ZIP, matched kernel RPM, four individual boot images, Fedora rootfs,
and compact release manifest. It omits the redundant bootset ZIP, standalone
copy of the support RPM, and large clean-install archive. Once that release is
complete, older GitHub releases are deleted so only one remains.

The manual workflow offers a **Force rebuild** option, port-version override,
desktop profile, and pinned Fedora compose inputs. Force rebuild creates new
component release tags even when matching builds exist. The kernel and Fedora
DNF package caches are Actions caches only: generated rootfs output stays
fresh, and cache data is kept outside the installroot/image. Kernel source
downloads are hash-verified before entering the cache. Cache misses affect
build time, not release correctness.

The user-facing `x810-fedora.yml` workflow is the single image and port-update
pipeline: it plans content fingerprints, builds only changed kernel/rootfs
components, assembles the matched aggregate, and independently builds the
updater-installable support RPM in a separate job. Both the support updater ZIP
and full-image assets are uploaded to the same release. Existing installed Tab
Companion versions that still name the retired `port-updates.yml` are mapped to
`x810-fedora.yml` by the app updater compatibility path. The deterministic
full-set publisher remains a checked-in shell script for readability and local
testing.

The manual reset workflow forces source rebuilds and prunes old published
releases/artifacts, but preserves Actions dependency caches (kernel ccache and
Fedora package caches) to keep the rebuild efficient.

The compact `manifest.json` records payload SHA-256/size, component
fingerprints, and build metadata. The support RPM is carried inside
`update.zip`; the release does not duplicate it as a standalone RPM.
Integrity hashes detect accidental or mismatched assets; they are not a
cryptographic signature. Rootfs builds pin Fedora compose repositories and source commits,
record package/source provenance, and normalize archive metadata; this does
not claim bit-for-bit reproducibility across all compiler/toolchain behavior.

The rootfs and boot-image builds also need the six exact X810 CYG1 Adreno
firmware blobs. Fedora's generic SM8550 files share the same names but are
rejected by the tablet's secure GPU loader. Do not add the proprietary blobs
or device-unique data to git or the public bundle. For a local build, stage
them from an owner-supplied extraction with:

```sh
python3 tools/x810-gpu-firmware.py stage \
  --source /path/to/X810-CYG1/vendor-extract/firmware \
  --dest local-assets/x810-gpu-firmware
```

GitHub Actions expects an `X810_GPU_FIRMWARE_URL` secret pointing to an
archive of those files (and `X810_GPU_FIRMWARE_TOKEN` if it is private). Each
blob is SHA-256 checked, and the final `vendor_boot.img` is checked to contain
the exact signed set before publication. A missing or mismatched set fails
the build rather than emitting a release that falls back to a TTY.

## Useful read-only commands

```sh
python3 tools/x810-install --help
python3 tools/x810-install --no-update
python3 tools/x810-install restore-boot-set --help
```

`backup-gpt` records GPT metadata only; it is not a partition-data backup.
Never use `rootfs/mk-internal-storage.sh` for dual boot: it formats the entire
userdata partition and leaves no Android userdata. The SD-card builder is a
legacy X710-derived path and is not the supported X810 install route.

## Maintainer reset build

For a clean CI sanity check, open **Actions → Reset X810 Fedora builds**, run it
on `main`, and type `RESET X810 FEDORA BUILDS`. It force-builds the kernel and
rootfs, rebuilds the matching support RPM and updater ZIP, then keeps only the
latest complete aggregate release. To preserve the Tab Companion run-ID feed,
the reset requires release-affecting sources and settings to match the last
successful main push, and republishes under that push's identity. Actions
dependency caches are preserved; the reset deletes superseded GitHub releases,
not build caches. If the build fails, the previous complete release is retained
and temporary releases are pruned. This is a PC/GitHub build operation; it does
not contact or modify a tablet.
