# Fedora on Samsung Galaxy Tab S9+ Wi-Fi (SM-X810)

An experimental native Fedora 44 / GNOME port for the Wi-Fi Galaxy Tab S9+
(`SM-X810`, `gts9pwifi`, Qualcomm SM8550). Fedora boots from the tablet's
internal UFS using a mainline-based Linux `7.2.0-gts9wifi` kernel. The project
supports a Fedora/One UI dual-boot layout; it is not an Android replacement
image and it is **only for the exact SM-X810 Wi-Fi model**. Do not use it on
the cellular SM-X816, Tab S9 Ultra, or another model.

The port boots and dual-boot switching has been exercised on the physical
tablet. The guided fresh-install script has host-side tests, but has **not**
been validated end-to-end from stock Android on hardware. Read the limitations
below and [`INSTALL.md`](INSTALL.md) before making partition changes.

## Hardware compatibility

✅ confirmed working on the tablet · 🟡 partial, limited, or pending a
validation step · ❌ unavailable · ❓ not tested

| Component | Status | Current X810 result |
|---|:---:|---|
| Display | ✅ | 2800×1752 panel; the user confirmed the selectable 120 Hz mode works. 60 Hz remains the default. |
| Desktop | ✅ | Fedora Workstation GNOME on Wayland/GDM boots and is usable. |
| GPU | ✅ | Adreno 740 hardware acceleration through Mesa Freedreno (OpenGL) and Turnip (Vulkan); not a software-rendering port. |
| Touchscreen | 🟡 | Touch and normal screen orientation work; mapping remains about 1 cm off and dragging can be finicky. |
| S Pen | 🟡 | Pen input and kernel-level palm rejection work; tilt is not implemented. Other dock/BLE features are not fully validated on X810. |
| EF-DX815 keyboard cover | 🟡 | The keyboard works, but the controller can occasionally stop sending input. Tab Companion includes a targeted controller-reset action; see below. |
| Cover touchpad | ❓ | Not separately qualified. |
| Wi-Fi | ✅ | WCN6855/ath11k works with the X810 firmware and board-data configuration. The port stays on kernel 7.2.0; later 7.2.1–7.2.6 stable kernels had a Wi-Fi regression. |
| Bluetooth | ✅ | Controller initializes; the X810 radio firmware/coexistence fix is included. |
| Built-in speakers | 🟡 | Stereo playback works on the current setup. A system-wide PipeWire/WirePlumber routing fix is in the port and its updater package; clean-install validation is pending. GNOME Settings' left/right output-test buttons have also been reported to freeze, so avoid those tests for now. |
| Microphones | ❓ | The ALSA/PipeWire capture source is present, but microphone recording has not been qualified. |
| Front and rear cameras | 🟡 | Sensor and libcamera support are integrated. Account permissions are fixed in the installer and port update; a fresh-login camera capture check is still pending. Rear focus is fixed/manual, not autofocus. |
| Hardware video decode | 🟡 | The VPU works through compatible stateful V4L2 M2M clients. Firefox and VLC do not currently use this path; the VPU encoder is untested. |
| Motion sensors / auto-rotation | ❌ | Sensor support is in progress, but the current system does not receive the required SSC QMI service, so tablet rotation is unavailable. |
| Battery and charging | 🟡 | Battery/charging support is present and the previous 96% charge cap is fixed. Samsung-equivalent battery aging behavior is not implemented. |
| USB-C, USB host, docks | 🟡 | USB host, charging/PD and dock support are present; not every accessory/display combination has been validated. |
| Haptics | 🟡 | Kernel support is prepared in source; physical vibration testing with the matching kernel is pending. |
| Suspend/resume | 🟡 | Not qualified for dependable daily use; freezes have been reported. Save work and avoid unattended suspend experiments. |
| Charging bypass | ❌ | No safe, verified Linux control exists yet. |
| Fingerprint reader | ❌ | The X810 secure-world provisioning/backend is incomplete; fingerprint login is not available. |

This table is a concise user-facing summary, not a substitute for subsystem
notes. See [`docs/Hardware-Notes.md`](docs/Hardware-Notes.md) for technical
details and [`docs/Known-Issues.md`](docs/Known-Issues.md) for the active issue
register and validation caveats.

## Install Fedora

### Before starting

- A Linux PC with Python 3, `adb`, `openssl`, and `sha256sum`.
- An **unlocked** SM-X810 bootloader, working Android root, and model-compatible
  TWRP. The tablet must be able to boot Android with USB debugging enabled so
  the wizard can check it.
- A complete backup of anything you want to keep. On a fresh dual-boot split,
  Android `userdata` is resized and formatted; Android apps, settings, and
  personal files on that partition will be erased. The installer saves GPT
  metadata and four boot images, **not** a personal-data backup.
- The vbmeta/TWRP setup required by the TWRP maintainer. This port does not
  publish a vbmeta image and does not write the recovery partition.

Install host tools if needed:

```sh
# Fedora
sudo dnf install android-tools openssl coreutils
# Debian / Ubuntu
sudo apt install adb openssl coreutils
# Arch
sudo pacman -S android-tools openssl coreutils
```

### Guided Linux-PC installation

Clone the repository, connect the tablet, approve the PC's ADB key in Android,
then run:

```sh
git clone https://github.com/iamSlightlyWind/x810-fedroid.git
cd x810-fedroid
python3 tools/x810-install
```

The wizard downloads the matching rootfs, kernel package and boot images from
the [latest aggregate release](https://github.com/iamSlightlyWind/x810-fedroid/releases/latest)
and verifies them against `manifest.json`. It
checks model/root/recovery state and partition geometry, asks for a Linux
username, full name, hostname, password and Android/Fedora storage sizes, and
leaves any unused capacity unpartitioned.

The flow is deliberately staged: it saves GPT metadata and the current
`boot`, `init_boot`, `vendor_boot`, and `dtbo` images on the PC, changes only
the Android `userdata` and Fedora `linuxroot` GPT entries, verifies the write,
and then **stops without rebooting**. Follow its prompt to enter TWRP manually
and resume the same install. On a fresh split, use TWRP's **Wipe → Format
Data** screen when prompted; the installer does not issue that command. It
then installs Fedora to `linuxroot`, provisions the chosen account and writes
only those four boot images. Recovery, vbmeta, GPT/PIT beyond the two specified
entries, bootloader, modem, EFS and calibration partitions are outside its
write path.

This installer is not yet a validated consumer flow: exact TWRP GPT refresh,
Android data reinitialization, rootfs extraction, first boot and recovery have
not all been tested together from stock Android on the physical tablet. Do not
ignore a failed model/geometry check or typed-confirmation warning. Full steps,
checkpoints, recovery commands and limitations are in [`INSTALL.md`](INSTALL.md).

## First Fedora boot and Tab Companion

After Fedora boots, install the Fedora/X810 Tab Companion package. Get
`tab-companion-fedora.zip` from the
[latest Tab Companion release](https://github.com/iamSlightlyWind/tab-companion/releases/latest),
extract it, and install the included RPM:

```sh
mkdir -p "$HOME/Downloads/tab-companion"
gh release download --repo iamSlightlyWind/tab-companion \
  --pattern tab-companion-fedora.zip --dir "$HOME/Downloads/tab-companion"
unzip -o "$HOME/Downloads/tab-companion/tab-companion-fedora.zip" \
  -d "$HOME/Downloads/tab-companion/package"
sudo dnf install "$HOME"/Downloads/tab-companion/package/*.rpm
```

If `gh` is not installed, download the same ZIP from the release page in a
browser, then run the `unzip` and `dnf install` commands. Open **Tab
Companion** from GNOME's app grid or run `tab-companion`.

- **Dualboot** shows the active system and stages the other saved boot set.
  It validates the X810 target, image sizes and checksums, and verifies the
  four partition writes. It does not reboot automatically; after it reports a
  successful switch, reboot manually when ready. Android's storage usage is
  unknown in Linux because Android encrypts `userdata`.
- **Cover keyboard troubleshooting** can collect diagnostic logs and, with
  authorization, reset the EF-DX815 STM32 controller if its keys stop
  responding. The current Fedora/X810 app package includes this action; update
  Tab Companion if it is absent. The reset rebinds only the controller driver;
  it does not read key values, update firmware, or alter storage.
  Keyboard/trackpad input pauses briefly during the reset. This is a recovery
  action, not a fix for the intermittent controller fault.
- The app also has S Pen, haptics and **Updates** sections; controls for
  unvalidated hardware should be treated as experimental.

The Android-side switcher is an optional, separate app. Its source and current
APK instructions are in the
[Tab Companion Android-app README](https://github.com/iamSlightlyWind/tab-companion/blob/main/android-app/README.md).
It requires Magisk root and pre-staged, verified boot sets; it only changes
the four boot partitions and does not install Fedora, repartition storage, or
repair TWRP/recovery.

## Update an installed system

Keep three kinds of updates separate:

1. **Tab Companion app:** open **Updates → Tab Companion → Check → Install
   update**. This updates the app package from its own repository.
2. **Fedora/X810 device support:** open **Updates → Fedora on Samsung Galaxy
   Tab S9+ Wi-Fi** (the Linux-port section), then **Check → Install update**.
   The port's rolling release contains an `update.zip` with
   the cumulative support RPM. The app checks the X810 target and package
   metadata before asking DNF to install it. This applies device-integration
   and userspace support fixes without replacing the whole Fedora rootfs.
3. **Kernel/boot set:** these are **not** installed by the in-OS updater.
   Kernel and boot-image releases must stay matched; the boot images are
   manually installed through TWRP using the release's instructions. `kernel.rpm`
   is a Fedora package, not a raw partition image. Do not flash it as an image
   or mix it with boot images from another release.

Ordinary Fedora packages remain managed by DNF. The latest X810 aggregate
release has `kernel.rpm`, `rootfs.tar.gz`, `update.zip`, `manifest.json`, and
the four boot-partition images. The manifest binds the files to their source
commit and build inputs. See
[`docs/TAB-COMPANION-PORT-RELEASE.md`](docs/TAB-COMPANION-PORT-RELEASE.md) for
what each artifact contains and how release/update matching works.

## Documentation

| Document | Contents |
|---|---|
| [`INSTALL.md`](INSTALL.md) | Full PC/TWRP install flow, preflight checks, checkpoints and recovery |
| [`docs/Hardware-Notes.md`](docs/Hardware-Notes.md) | Hardware behavior, drivers, measured limitations and diagnostics |
| [`docs/Known-Issues.md`](docs/Known-Issues.md) | Current issue register and validation state |
| [`docs/Device-Controls.md`](docs/Device-Controls.md) | GNOME controls and device-control command-line interface |
| [`docs/x810-research/TAB-COMPANION.md`](docs/x810-research/TAB-COMPANION.md) | X810 dualboot app boundaries, boot sets and recovery |
| [`docs/x810-research/BOOT-STRATEGY.md`](docs/x810-research/BOOT-STRATEGY.md) | X810 boot chain and partition safety notes |
| [`docs/x810-research/HARDWARE-X810.md`](docs/x810-research/HARDWARE-X810.md) | Device-specific research notebook |
| [`docs/TAB-COMPANION-PORT-RELEASE.md`](docs/TAB-COMPANION-PORT-RELEASE.md) | Port updater package and release contents |

## Build and project scope

This repository owns the X810 kernel/device tree, Fedora rootfs, port support
package, firmware staging and Linux-PC installer. Tab Companion's app and
update UI are maintained separately in
[`iamSlightlyWind/tab-companion`](https://github.com/iamSlightlyWind/tab-companion).
The main [`X810 Fedora build and release`](.github/workflows/x810-fedora.yml)
workflow builds changed components, assembles one matched release and retains
only the latest complete aggregate. This automation makes builds easier to
repeat; it does not substitute for validating a fresh install on hardware.

The port uses the
[X710 Fedora port](https://github.com/nacht20-de/gts9wifi-fedora-linux) as
Linux/Fedora prior art, exact X810 CYG1 Samsung source as a hardware reference,
and the [X910 Ubuntu Tab S9 Ultra project](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra)
as Tab Companion/dual-boot design prior art. X910 partition numbers, images and
device assumptions are not reused as X810 evidence.

## AI assistance

This experimental, unofficial project was developed with AI assistance.
Review the source and validate hardware claims before use; it is not an
official Samsung or Fedora release.
