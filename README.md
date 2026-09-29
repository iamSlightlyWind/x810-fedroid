# Fedora on Samsung Galaxy Tab S9+ Wi-Fi (SM-X810)

This project brings Fedora Workstation 44, GNOME, and a device-specific Linux
kernel to the Wi-Fi-only Samsung Galaxy Tab S9+ (`SM-X810`, `gts9pwifi`,
Qualcomm SM8550). Fedora runs from the tablet's internal UFS storage alongside
One UI; this is **not** an Android replacement image.

> **Experimental / device-specific.** Use only on the exact **SM-X810**. Do not
> use these images or instructions on the cellular SM-X816, Tab S9 Ultra, or
> another device. Partitioning changes and a fresh dual-boot setup erase
> Android `userdata`. Back up anything important and make sure you can reach
> Download Mode and TWRP before proceeding.

The tablet has booted both Fedora and Android, and switching between their boot
sets has been exercised on hardware. The Linux-PC guided installer has host-side
tests, but the complete stock-Android-to-Fedora install/recovery flow has **not**
been validated end-to-end on the physical tablet. Read [`INSTALL.md`](INSTALL.md)
before changing partitions.

## Hardware compatibility

Status key: ✅ confirmed on the tablet · 🟡 partial, limited, or awaiting a
specific validation · ❌ not working/unavailable · ❓ not tested.

| Component | Status | X810 status |
|---|:---:|---|
| Display | ✅ | 2800×1752 AMOLED; 120 Hz is selectable and the owner has confirmed it works. 60 Hz remains the default. |
| Desktop | ✅ | Fedora GNOME on Wayland/GDM boots and is usable. |
| GPU / rendering | 🟡 | Adreno 740 hardware acceleration works through Mesa Freedreno (OpenGL) and Turnip (Vulkan). GTK app/widget artifacts have appeared inconsistently in testing; a GTK/Vulkan setting removed them in one test, but the result was not a reproducible driver-level fix. |
| Touchscreen | 🟡 | Touch and normal orientation work; the pointer remains about 1 cm offset and dragging can be finicky. |
| S Pen | 🟡 | Pen input and kernel-level palm rejection work. Tilt and some dock/Bluetooth features are unimplemented or unverified. |
| EF-DX815 keyboard cover | 🟡 | Keyboard input works but may stop intermittently. Tab Companion offers a controller reset; it is recovery, not a confirmed permanent fix. |
| Cover touchpad | ❓ | Not separately qualified. |
| Wi-Fi | ✅ | WCN6855/ath11k works with X810 firmware and board data. The port is pinned to kernel 7.2.0; later 7.2.1–7.2.6 stable kernels had a Wi-Fi regression. |
| Bluetooth | ✅ | Controller and X810 radio firmware/coexistence configuration work. |
| Speakers | ✅ | Stereo output works; the PipeWire/WirePlumber routing fix is packaged and owner-confirmed. The GNOME left/right test-button UI is not needed to validate playback. |
| Microphones | 🟡 | A Linux candidate now uses the reference-backed VA-macro capture backend while retaining CYG1's physical DMIC3/1 recording pair. Short raw captures show nonzero signal, stronger on that mapping, but controlled speech and normal GNOME/PipeWire input remain unverified. |
| Cameras | 🟡 | Front/rear sensors enumerate and libcamera support is integrated; a normal desktop capture flow still needs validation. Rear focus is fixed/manual, not autofocus. |
| Hardware video decode | 🟡 | Stateful V4L2 decode was verified with owner-supplied X810 CYG1 firmware. Public releases omit that proprietary firmware; browser/VLC acceleration and encoding are not established. |
| Qualcomm NPU / HTP | ❌ | CDSP remains disabled and no Fedora QNN/HTP runtime is integrated. The port now preserves the X810 CDSP firmware carveouts, but inference has not been enabled or tested. |
| Motion sensors / auto-rotation | 🟡 | The installed system now discovers SSC QRTR service 400, and SensorProxy reports an accelerometer/orientation property. Physical GNOME auto-rotation has not yet been verified on this boot. |
| Battery / charging | 🟡 | Battery telemetry and charging work; the prior 96% cap is fixed. Not all chargers and charge behaviors have been validated. |
| USB-C / powered USB hub | ✅ | The owner has confirmed that a powered USB hub supplies power to the tablet. Data passthrough and other host, dock, and display combinations are not all tested. |
| Haptics | 🟡 | Kernel/DTB support is in the latest standalone kernel release; physical vibration validation on that kernel is pending. |
| Suspend / resume | 🟡 | Resume is not qualified. The support update masks systemd sleep targets and ignores cover-close suspend as a freeze-prevention mitigation; it does not fix the kernel wake path. |
| Fingerprint reader | ❌ | Authentication is unavailable. EL721/K250A nodes and kernel drivers are present on the running tablet, but there is no Fedora EL721 `libfprint` backend, verified X810 calibration set, or safe SPSS/QTEE owner lifecycle. |
| Charging bypass | ❌ | No safe, verified Linux control is available. |

These statuses describe what has been observed on this device, not promises for
other units or every build. See [`docs/Hardware-Notes.md`](docs/Hardware-Notes.md)
and [`docs/Known-Issues.md`](docs/Known-Issues.md) for caveats and subsystem
details.

Moonlight V4L2 hints are available through an opt-in launcher. Flathub and
Moonlight are not added automatically; install/update explicitly with
`sudo x810-moonlight-install`. Live-stream hardware decoding remains
unverified. See [the Moonlight V4L2 experiment](docs/x810-research/MOONLIGHT-V4L2-EXPERIMENT.md).

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
