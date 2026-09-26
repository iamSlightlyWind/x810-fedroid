# Install Fedora on the Galaxy Tab S9+ Wi-Fi (SM-X810)

## Quick start (Linux PC)

Clone this repository, connect the tablet by USB, then run:

```sh
python3 tools/x810-install
```

In a terminal, that starts the guided installer and downloads the latest
published X810 clean-install bundle. To launch the optional text menu instead,
run `python3 tools/x810-install menu`. For an offline bundle, use
`python3 tools/x810-install install --bundle /path/to/x810-clean-install.tar.gz`.

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
at least the minimum recorded by the verified clean-install bundle.

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
   extents, saves a credential-free checkpoint, and **stops without rebooting**.
4. Manually reboot into TWRP so recovery rereads GPT. Resume with the same
   bundle and the backup path printed by the script:

   ```sh
   python3 tools/x810-install install \
     --bundle ./x810-fedora-sm-x810-VERSION-clean-install.tar.gz \
     --resume-from ./x810-install-backup-TIMESTAMP
   ```

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

The clean-install bundle currently installs Fedora and its X810 support RPM;
it does **not** include the separate Tab Companion application package. After
the first Fedora boot, install the current Fedora RPM from the Tab Companion
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

The checkpoint binds the model/codename, ADB serial, TWRP version, bundle
manifest hash, exact partition extents, and local backup checksums. It contains
no password or password hash. A changed layout, bundle, TWRP version, boot set,
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

The guided flow, clean-bundle verifier, split arithmetic, mocked GPT path,
account provisioning, and boot-image write/restore paths have host-side tests.
The installer has **not** been validated end-to-end on a physical SM-X810 from
stock Android. In particular, the exact TWRP GPT refresh, Android data
reinitialization, rootfs extraction, first boot, and recovery sequence still
need a physical validation run. Treat it as experimental, not a proven
consumer installer. Do not use the developer-only `split --write` command as
a substitute for the guided flow.

The repo can assemble a clean-install bundle only after matching kernel and
rootfs release assets exist. No release was built or published by this code
change. To create a release after committing/pushing the repository, use the
Actions workflows in this order:

1. **Kernel build** (`.github/workflows/kernel.yml`) — choose a unique
   `kernel_release` tag; the source tarball hash and Fedora toolchain inputs
   are pinned.
2. **Fedora rootfs** (`.github/workflows/rootfs.yml`) — select the kernel tag
   just produced, provide a numeric `port_version` such as `1.0.0`, and choose
   a unique `release_tag`.
3. **Full set release** (`.github/workflows/full-set.yml`) — select both
   source tags, use the same `port_version`, and provide a new combined
   `release_tag`.

The full-set workflow publishes the updater index and a separate deterministic
`x810-fedora-sm-x810-<tag>-clean-install.tar.gz` with a schema-1 manifest,
SHA-256/size inventory, matched rootfs and kernel module release, and exactly
four boot images. The bundle excludes `vbmeta` and recovery images. Integrity
hashes detect accidental or mismatched assets; they are not a cryptographic
signature. Rootfs builds pin Fedora compose repositories and source commits,
record package/source provenance, and normalize archive metadata; this does
not claim bit-for-bit reproducibility across all compiler/toolchain behavior.

The Fedora rootfs build currently obtains firmware from its declared,
checksum-pinned inputs. Review those sources and their distribution terms
before publishing a release; do not add personal CYG1 firmware or device-unique
data to the repository or bundle.

## Useful read-only commands

```sh
python3 tools/x810-install doctor
python3 tools/x810-install install --bundle ./bundle.tar.gz --plan-only
python3 tools/x810-install backup-gpt --out ./x810-gpt-backup
python3 tools/x810-install plan-split ./x810-gpt-backup/gpt --android-percent 50
python3 tools/x810-install release
```

`backup-gpt` records GPT metadata only; it is not a partition-data backup.
Never use `rootfs/mk-internal-storage.sh` for dual boot: it formats the entire
userdata partition and leaves no Android userdata. The SD-card builder is a
legacy X710-derived path and is not the supported X810 install route.
