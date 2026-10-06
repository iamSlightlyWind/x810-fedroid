# Fedora on Samsung Galaxy Tab S9+ Wifi

Fedora 44 AARCH64 for the Samsung Tab S9+ Wifi (SM-X810) with Linux kernel 7.2.0, with dual boot capability using [Tab Companion](https://github.com/iamSlightlyWind/tab-companion)

<img width="4000" height="3000" alt="20261004_092023" src="https://github.com/user-attachments/assets/906614a0-6dca-43f4-842e-93cd230166f1" />


## Hardware compatibility

Status key: ✅ confirmed on the tablet · 🟡 partial, limited, or awaiting a specific validation · ❌ not working/unavailable · ❓ not tested.

| Component | Status | X810 status |
|---|:---:|---|
| Display | ✅ | 2800×1752 AMOLED up at 120 Hz |
| Desktop | ✅ | GDM, Gnome 50.5 on Wayland |
| GPU / rendering | ✅ | Adreno 740 hardware acceleration |
| Touchscreen | 🟡 | Still some touchscreen offset, up to 5mm |
| S Pen | 🟡 | No S Pen to test |
| Book keyboard cover | ✅ | EF-DX815 works |
| Wi-Fi | ✅ | WCN6855/ath11k |
| Bluetooth | ✅ | Mouse and Earbuds tested working |
| Speakers | ✅ | All four speakers works |
| Microphones | ✅ | Mic works |
| Cameras | ✅ | Both cameras work |
| Hardware video decode | ✅ | Verified working and recognizable by Moonlight |
| Qualcomm NPU / HTP | ❌ | CDSP remains disabled and no Fedora QNN/HTP runtime is integrated |
| Motion sensors / auto-rotation | 🟡 | Motion sensors and SensorProxy work; GNOME currently has no auto-rotate option/integration |
| Battery / charging | 🟡 | Finicky. Direct charging works but usb hub power passthrough charges slowly and sometime doesnt charges |
| USB-C / powered USB hub | ✅ | USB devices works |
| External monitor | ✅ | HDMI/Type-C monitors work |
| Haptics | ✅ | Physical tablet vibration works through the kernel haptics path |
| Suspend / resume | ✅ | Suspend and waking up works |
| Fingerprint reader | ❌ | Not working |
| Charging bypass | ❌ | No safe, verified Linux control is available |

See [`docs/Hardware-Notes.md`](docs/Hardware-Notes.md) and [`docs/Known-Issues.md`](docs/Known-Issues.md) for caveats and subsystem details. Updates via [Tab Companion](https://github.com/iamSlightlyWind/tab-companion) can resolves these issues if patches are available.

## Install Fedora

The supported entry point is the guided installer on a Linux PC. Requirements:

- Exact SM-X810 with an **unlocked bootloader**, Android root, USB debugging,
  and a compatible TWRP recovery already available.
- The TWRP/vbmeta setup specified by the TWRP maintainer. This project does not
  provide a vbmeta image or install recovery.
- Linux PC with Python 3, `adb`, `openssl`, and `sha256sum`; install the latter
  three from your distribution's packages if missing.
- A backup. A fresh split resizes and formats Android `userdata`, erasing its
  apps, settings, and files. The installer saves GPT metadata and four boot
  images to the PC, **not** your personal data.

Clone the repo, boot Android, enable USB debugging, approve the PC's ADB key,
connect the tablet, then run:

```sh
git clone https://github.com/iamSlightlyWind/x810-fedroid.git
cd x810-fedroid
python3 tools/x810-install
```

The wizard checks the model, root/recovery state, and partition geometry; asks
for a Linux account and storage sizes; downloads the matching release assets;
and verifies their manifest. It is deliberately staged: it backs up selected
metadata and boot images, changes only the specified Android/Fedora partition
entries, then stops for you to reboot into TWRP manually. On a fresh split, you
must use TWRP's **Wipe → Format Data** when prompted. The installer does not
reboot the tablet for you. The installer writes Fedora to `linuxroot` and
updates its four boot images; it does not flash the bootloader, modem, EFS,
calibration partitions, recovery, or vbmeta.

**Do not bypass a failed identity/geometry check or confirmation.** The exact
TWRP partition refresh, Android data reinitialization, full rootfs install,
first boot, and recovery path still need an end-to-end hardware validation.
Follow the detailed checkpoints and recovery notes in [`INSTALL.md`](INSTALL.md).

## Tab Companion

The modified [Tab Companion](https://github.com/iamSlightlyWind/tab-companion)
is maintained in its own repository; this Fedora port supplies its device
support and Linux-port updates. The initial Fedora setup does not install the
separate desktop application. After the first Fedora boot, install its Fedora
RPM from the latest Tab Companion release:

```sh
mkdir -p "$HOME/Downloads/tab-companion"
gh release download --repo iamSlightlyWind/tab-companion \
  --pattern tab-companion-fedora.zip --dir "$HOME/Downloads/tab-companion"
unzip -o "$HOME/Downloads/tab-companion/tab-companion-fedora.zip" \
  -d "$HOME/Downloads/tab-companion/package"
sudo dnf install "$HOME"/Downloads/tab-companion/package/*.rpm
```

If you do not use GitHub CLI (`gh`), download `tab-companion-fedora.zip` from
the [release page](https://github.com/iamSlightlyWind/tab-companion/releases)
in a browser, then run the `unzip` and `dnf install` commands above. Launch
**Tab Companion** from GNOME's app grid or run `tab-companion`.

The app includes:

- **Dualboot:** shows the active system and prepares the other saved boot set.
  It checks that the images target X810, verifies them and checks partition
  writes. It does not reboot automatically; reboot manually after a successful
  switch. Android's encrypted `userdata` usage cannot be measured from Fedora.
- **Keyboard troubleshooting:** can reset the EF-DX815 STM32 controller when
  input stops. A separate optional 20-second diagnostic capture records key
  presses/releases only after explicit authorization; it is saved locally and
  not sent automatically, so do not type passwords or sensitive information
  during a capture. The reset pauses input briefly and changes no firmware or
  storage. It is a recovery action, not a permanent fix for the intermittent
  issue.
- **Updates:** separate update checks for Tab Companion and Fedora device
  support. Hardware controls not listed as confirmed above should be treated as
  experimental.

An optional Android-side switcher is also maintained in the app repository. It
requires Android root and pre-staged boot sets; it only switches boot images.
It does not install Fedora, repartition storage, or repair recovery. See the
[Android-app instructions](https://github.com/iamSlightlyWind/tab-companion/blob/main/android-app/README.md).

## Updating

There are three separate update paths:

1. **Tab Companion:** in the app, open **Updates → Tab Companion**, check, then
   install the app update.
2. **Fedora device support:** open the Linux-port entry in **Updates**, check,
   then install. The Fedora port release provides a cumulative support RPM in
   `update.zip`; Tab Companion installs it through DNF. It updates device
   services/configuration and userspace fixes without replacing the Fedora
   root filesystem.
3. **Kernel and boot images:** these are **not** applied by the in-OS updater.
   Install the matching boot-image set manually through TWRP, following the
   release instructions. `kernel.rpm` is an RPM package, **not** a raw image to
   flash. Keep the boot images and kernel release matched; do not mix files
   across releases.

Regular Fedora software updates continue to use DNF. The port updater is for
X810-specific support, not a general Fedora upgrade or whole-rootfs migration.
See [`docs/TAB-COMPANION-PORT-RELEASE.md`](docs/TAB-COMPANION-PORT-RELEASE.md)
for release contents and update matching.

## Documentation

| Document | Contents |
|---|---|
| [`INSTALL.md`](INSTALL.md) | Full Linux-PC/TWRP install, checkpoints, and recovery |
| [`docs/Hardware-Notes.md`](docs/Hardware-Notes.md) | Hardware behavior, drivers, limitations, and diagnostics |
| [`docs/Known-Issues.md`](docs/Known-Issues.md) | Open issues and validation status |
| [`docs/Device-Controls.md`](docs/Device-Controls.md) | GNOME device controls and command-line interface |
| [`docs/x810-research/BOOT-STRATEGY.md`](docs/x810-research/BOOT-STRATEGY.md) | Boot-chain and partition safety notes |
| [`docs/x810-research/TAB-COMPANION.md`](docs/x810-research/TAB-COMPANION.md) | Dualboot boundaries, boot sets, and recovery |
| [`docs/TAB-COMPANION-PORT-RELEASE.md`](docs/TAB-COMPANION-PORT-RELEASE.md) | Linux-port updater and release contents |

## Project and contributions

This repository contains the X810 kernel/device tree, Fedora rootfs integration,
support package, firmware staging and Linux-PC installer. The Tab Companion
application and its Android companion live in the separate repository linked
above. The X810 GitHub Actions workflow builds changed components, assembles a
matched release and retains the latest aggregate package; repeatable builds do
not replace physical-device validation.

The project draws on the
[X710 Fedora port](https://github.com/nacht20-de/gts9wifi-fedora-linux) and the
[X910 Ubuntu Tab S9 Ultra project](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra)
as prior art. Their device-specific images and partition assumptions are not
used as X810 evidence. Contributions should be reproducible from the
repository, and hardware claims should distinguish source/build status from
tests on the physical SM-X810.

## Firmware, licensing, and AI assistance

Proprietary Samsung/Qualcomm firmware is not stored in this repository. The
project is unofficial and is not affiliated with Samsung or Fedora; per-file
license headers and imported-source notices apply.

AI tools assisted with parts of the research, code, and documentation. AI
output can be wrong: review changes and verify hardware claims before relying
on them.
