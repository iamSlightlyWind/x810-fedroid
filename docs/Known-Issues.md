# Known Issues

The port's live issue register. Numbers are the original report ids and stay
stable — other pages and build scripts reference them — so they are never
reused, and a retired number is simply absent rather than reassigned
(that is why there is no 8 or 12).

| # | Issue | Status |
|---|---|---|
| 1 | Battery percentage capped at 96 % | fixed — the charger's float voltage was never programmed, so the pack charged 60 mV short |
| 2 | No palm rejection (S Pen) | fixed — pen proximity now suppresses touchscreen input |
| 3 | Bluetooth lag under 2.4 GHz Wi-Fi | fixed — Samsung NVM/rampatch substituted for the generic ones |
| 4 | Rear camera (13 MP HI1337 + DW9808 lens) | works — manual focus only; a fixed focus of 384 ships |
| 4b | Front camera (12 MP HI1337) | works |
| 5 | GNOME auto-rotation option/integration | open — motion sensors and SSC/SensorProxy work, but GNOME currently exposes no auto-rotate option; see #25 |
| 6 | USB debug link flaky | fixed — RNDIS gadget converted to ECM |
| 7 | Weak 5 GHz Wi-Fi RX | fixed — board-data (BDF) substitution, ~47 dB improvement |
| 9 | Discord/Roblox unreachable (DPI) | fixed — kernel rebuilt with `nfqueue` |
| 10 | Front camera did not probe | fixed — wrong I2C address in the DTS |
| 11 | libcamera had no sensor helper, so max gain | fixed — helper added, AGC runs |
| 13 | Charging bypass on 25 W+ chargers | open — Linux driver/API support and verified hardware semantics are missing; not a GNOME-only patch |
| 14 | Double tap to turn on the screen | fixed — with a GNOME extension UI |
| 15 | Under-display fingerprint sensor (EgisTec EL721) | open — live EL721/K250A devices and kernel modules are present, but Fedora has no EL721 `libfprint` backend; X810 calibration, FTS1BA90A FOD integration, and safe SPSS/QTEE ownership remain unresolved ([audit](x810-research/FINGERPRINT-LIVE-STATUS.md)) |
| 16 | Hardware video decode (iris / VPU 3.0) | fixed for supported stateful V4L2 clients — exact X810 CYG1 firmware is in clean builds and support-RPM updates; FFmpeg/GStreamer hardware decode works. Browser/VLC acceleration and Moonlight live-stream selection remain unsupported or unverified |
| 17 | Speaker volume capped (~−19 dB) | fixed — Cirrus speaker-protection DSP firmware now loads |
| 18 | `/`, `/etc`, `/usr` owned by the image build user | fixed — this had silently disabled *every* `tmpfiles.d` entry |
| 19 | Kernel log flooded by ADSP handover messages | fixed — the repeat is logged at debug level now |
| 20 | Wi-Fi dead on the 7.2.1–7.2.6 stable kernels | open — pinned to 7.2.0 |
| 21 | PipeWire speaker streams fail to link | fixed — system-wide WirePlumber configuration is packaged, and the owner confirms stereo playback works |
| 22 | GNOME camera clients cannot open `root:video` camera nodes | fixed in installer and support-RPM upgrade path; front/rear camera capture is confirmed working |
| 23 | No 120 Hz display mode | fixed — user confirmed 120 Hz works on-device; 60 Hz remains the default |
| 24 | ADSP/sensorspd start ordering around panel coldboot recovery | confirmed on the latest boot: installed ordering drop-ins match source, and ADSP/rootpd/sensorspd are active; see #25 for the separate GNOME auto-rotation integration issue |
| 25 | SSC sensor discovery and tablet auto-rotation | sensor discovery is working, but GNOME auto-rotation remains open: the desktop currently has no auto-rotate option/integration |
| 26 | No GNOME power-profile/governor switcher | live root cause fixed: explicitly load `icc_osm_l3` so CPUFreq policies exist; profile switching verified over D-Bus, persistent RPM/boot validation pending |
| 27 | Kernel rejects optional module BTF after boot/module builds differ | mitigation added: allow the module to load without its mismatched BTF metadata; exact boot/module matching is still preferred |
| 28 | Deep suspend can freeze and fail to wake | mitigated in the reproducible overlay: lid close ignores suspend and sleep targets are masked; root cause still needs X810 wake-source tracing |
| 30 | Device-wide GPU startup/rendering | exact X810 CYG1 Adreno firmware is embedded in rootfs and vendor_boot; the current boot reports hardware-accelerated Freedreno FD740 through GLX |
| 31 | Fine visual artifacts in GTK4 controls | mitigated by the global `GSK_GPU_DISABLE=merge` profile in rootfs/updater RPM; owner reported clean UI at normal speed with Vulkan/Turnip retained. This is a userspace workaround, not a driver fix |
| 32 | External-display output/modesetting | open — current external-only mode shows a solid blue screen; the tablet panel works normally after disconnecting the monitor. Kernel DPU resource-reassignment patch is present, but does not fix this symptom |

Haptics are enabled in the kernel source (stock-active-high GPIO18 plus
`gpio-vibra`) and included in the kernel release; physical vibration is confirmed
working on the tablet.

Also outstanding, not in the numbered register:

- S Pen tilt is exposed as `ABS_TILT_X/Y`; live vector orientation and drawing-app behavior remain unverified (see `docs/Hardware-Notes.md`);
- built-in microphone capture works with the VA-macro/DMIC configuration and current PipeWire setup. See `docs/x810-research/AUDIO-PIPEWIRE.md` and `tools/diagnose-x810-mic.sh`;
- the Android `/vendor` mount has a shipped read-only logical-partition mapper;
  confirm the mount on-device after the next update (see below);
- SELinux remains permissive, but first boot now requests Fedora's standard full
  autorelabel because TWRP extraction drops SELinux xattrs. The support RPM
  performs a one-time relabel migration and schedules the standard relabel
  reboot for installs created before this fix;
- file capabilities are lost when the rootfs is packed (see below);
- the VPU encoder node `/dev/video18` is untested;
- early-boot timestamps read 1970 — the RTC has no valid time before NTP;
- ADSP/sensorspd must not be started outside the panel-ordered sensor-proxy
  path. A failed SSC attach can still reset the shared ADSP, so do not start
  the ADSP units manually or race them against panel recovery;
- the patched userspace is built from source at image build time rather than
  shipped from a Fedora repository — `hexagonrpcd` (two patches),
  `iio-sensor-proxy` with libssc, and `libssc`/`pd-mapper` (not in Fedora at
  all); a COPR is the obvious home;
- the GNOME extension ships as a plain directory — GNOME Shell does not rescan
  extension directories at runtime, so it needs proper packaging;
- browsers and VLC still decode in software: the VPU has no VA-API driver. Two
  routes: a VA-API driver over the stateful M2M node (the surviving
  VA-API-over-V4L2 projects are ~5,000–6,000 lines of C for three codecs, but
  both target the stateless API), or a Chromium build with
  `use_v4l2_codec=true` (an unofficial build path, unsupported by Google).

---

## Open issues in detail

### 26 — GNOME power profiles on Qualcomm CPUFreq

The `tuned-ppd` D-Bus API was already active, but the CPU frequency policies
were absent: `qcom-cpufreq-hw` deferred until the SM8550 OSM L3 provider loaded.
Manually loading `icc_osm_l3` on the running tablet created all three policies;
a short D-Bus test switched them to `performance` and restored `balanced` /
`schedutil`. The support RPM now loads the module during an update and at each
boot through `modules-load.d`. The generic TuneD `powersave` profile also
prefers `schedutil` when available, so Power Saver could still allow high
benchmark frequencies. The support RPM now maps Power Saver to an X810-specific
profile forcing CPUFreq `powersave`. Verify all three active governors and
benchmark/temperature behavior on-device. This is CPUFreq-level control, not a
Samsung power HAL; performance mode can increase heat and power draw.

### 27 — Optional module BTF mismatches

One boot showed the kernel BTF verifier rejecting the `nf_tables` module's
optional type metadata (`ENUM (anon)`, `Invalid name`, BTF `-22`). The log
proves a module-BTF validation failure, not why it happened. A plausible cause
is that boot files and the rootfs module tree can be updated separately while
both builds share the same `uname -r`, so a stale module tree may not be
obvious. The kernel config now enables
`CONFIG_MODULE_ALLOW_BTF_MISMATCH`: on this error Linux drops that module's BTF
debug metadata and allows the actual module to load. This does not repair
stale type data, and BPF programs that need the affected module's types will
not have that metadata. Keep the boot bundle and `kernel.rpm` from the same
release whenever possible; verify that `nf_tables` and firewall/NFQ modules
load after the next kernel update.

### 24 — ADSP/sensorspd start ordering around panel coldboot recovery

The sensor-proxy unit now explicitly requests `hexagonrpcd-adsp-sensorspd`
using `Wants=` after required panel recovery; `After=` alone does not start a
disabled unit. The sensorspd drop-in then requests the ADSP boot helper, FastRPC
node readiness and writable HexagonFS tree in order. This keeps direct
standalone autostart disabled while giving the sensor-proxy recovery path a
deterministic dependency chain.

The latest live boot has the source-matching ordering drop-ins installed and
the ADSP, rootpd, and sensorspd active. The sensor-proxy recovery subsequently
completed after an SSC response. This validates the startup chain on this boot,
but not physical display rotation; see #25. Do not manually start either ADSP
unit or race it against panel recovery.

### 25 — SSC sensor discovery and tablet auto-rotation

The startup chain now stages a private `/run` HexagonFS tree from this tablet's
mounted CYG1 `/vendor/etc/sensors` and `/mnt/vendor/persist/sensors/registry`
before attaching `hexagonrpcd` to `sensorspd`. It validates that the persistent
registry cache matches the vendor JSON mtimes, copies the files without
changing Android partitions, and supplies the target's socinfo selector values
where mainline sysfs does not expose Samsung's aliases. The desktop proxy then
waits for the SSC endpoint.

An earlier live failure found `gts9wifi-adsp-boot` running and
`hexagonrpcd-adsp-sensorspd` active, while `hexagonrpcd-adsp-rootpd` was
inactive: systemd had skipped it at multi-user because its
`/dev/fastrpc-adsp` condition was checked before deferred ADSP boot created the
node. The rootpd override now `Requires=` and follows the same panel-ordered
`gts9wifi-adsp-boot.service`, preventing that early condition skip without
starting ADSP ahead of panel recovery.

On the 2026-09-29 live boot, a read-only check found the current recovery
oneshot completed successfully after SSC responded on its first probe;
`hexagonrpcd-adsp-sensorspd` and `iio-sensor-proxy` were active,
`qrtr-lookup 400` returned the Snapdragon Sensor Core service, and SensorProxy
reported `HasAccelerometer=true` with orientation `normal`. GNOME's
`orientation-lock` setting was `false`. The live FastRPC udev database also
contained the `ssc-accel` discovery tag and configured board mount matrix.
Thus SSC discovery and the orientation API are present and motion-sensor
operation is confirmed. GNOME currently exposes no auto-rotate option, so
desktop auto-rotation remains an integration/UI issue; suspend/resume recovery
remains unverified. The proxy's log that the
firmware matrix is all zero is its documented identity-matrix fallback before
the configured udev matrix is applied, not evidence that the udev rule is
missing.

The support update uses the same hash-locked `libssc`/`iio-sensor-proxy`
builder as a clean image and adds an early-claim race guard in the proxy. It
also replaces donor-model sensor inputs with a checked runtime composition of
the tablet's own stock config and persist registry. The installed service and
udev-rule files match repository source; the installed recovery helper differs
only in its shell interpreter path. GNOME auto-rotation UI/integration remains
open. Do not manually restart remoteproc or sensorspd while the tablet is
unattended, since a failed attach can interrupt audio.

### 13 — Charging bypass on 25 W+ chargers

Running the tablet from the adapter instead of the battery pack, with a toggle
in *GNOME Settings → Power*.

This is not currently a GNOME-only issue. Although the power-supply core
defines a generic `Bypass` charge type, the X810 SM5714 and SM5440 drivers do
not expose `charge_type`/`charge_types` or a bypass register-control path in
the current source. The cited `battery,ovp_bypass_mode` DT property has not
been demonstrated to be a usable Linux control on this device. UPower 1.91.4
uses charge types internally for its charge-threshold implementation, but
does not expose a generic arbitrary charge-type setter; GNOME Control Center
uses the threshold API instead. A real toggle therefore needs hardware/driver
capability verified first, then a kernel ABI, a UPower API and a GNOME UI —
not just a small control-center patch. Do not write charger registers or
advertise bypass until the electrical and thermal semantics are established.

### 15 — Under-display fingerprint sensor (EgisTec EL721)

A previous read-only tablet snapshot found no `/dev/esfp0` or `/dev/k250a`, no
`egis_el721`/`snvm` modules under the running `7.2.0-gts9wifi`, and only
`qcomtee` loaded. That observation predates the latest standalone kernel
release, so it does not establish the state after installing it. Release
[`x810-kernel-4b02b0c61f8e6090`](https://github.com/iamSlightlyWind/x810-fedroid/releases/tag/x810-kernel-4b02b0c61f8e6090)
(source commit `562374ff64ce7e9ea8ae844768bd548cb88c4099`) contains both
modules and the CYG1 board-id 04 GPIO mapping. The latest complete Fedora set,
[`x810-fedora-port-build-36381339176`](https://github.com/iamSlightlyWind/x810-fedroid/releases/tag/x810-fedora-port-build-36381339176),
is older (commit `e481356c9c06e97920d52765f5668cea2034ad06`); neither artifact
proves that the updated kernel is installed. See
[`FINGERPRINT-EL721-DTBO.md`](x810-research/FINGERPRINT-EL721-DTBO.md) for the
source/package evidence.

The panel exposes `cell_id`, `fod_mode` and `fod_ready` attributes, which does
not establish that the biometric stack works. Earlier sessions reported TEE
status `KEYMASTER_NOT_CONFIGURED` / cache `9936`; it has not been revalidated.

There is no supported X810 `libfprint` backend or validated GNOME/GDM/PAM
enrollment path in this repository. Do not invoke secure-owner/Keymaster
provisioning or touch credentials as a diagnostic step. After the matching
kernel/boot set is installed, first verify module presence and device binding
with read-only probes; authentication needs a separate, security-reviewed
implementation.

Notes:

- The intended module list is in
  `modules-load.d/gts9wifi-fingerprint.conf`; it cannot create device nodes if
  modules are absent from the running kernel. Recheck after installing the
  latest matching kernel/boot set.
- Judge the trustlet read-only before porting anything, with a known-resident
  control, and beware that the TA lookup needs **three** parameters, not the two
  the reference documentation suggests.

### `/vendor` — source fix shipped; on-device validation pending

The working system needs the Android partitions at runtime, not just at
first-boot extraction:

- `gts9wifi-android-parts.service` parses the Android LP metadata on
  `/dev/disk/by-partlabel/super`, then creates and verifies a read-only
  `dm-linear` mapping at `/dev/mapper/vendor`;
- `super/vendor` is **erofs** and mounts read-only at `/vendor`;
- the `dsp` partition (ext4) mounts at `/vendor/dsp`;
- the `persist` partition mounts **read-write** at `/mnt/vendor/persist`,
  because Samsung's sensor registry writes and Wi-Fi calibration live there.

The mapper, systemd units, and support-RPM enablement are implemented in the
port source. The mapper fails closed on unsupported or invalid LP metadata and
does not write to `super`. The synthetic metadata tests pass, but successful
mounting against the tablet's live partitions has not yet been confirmed; check
`systemctl status gts9wifi-android-parts.service vendor.mount` and
`findmnt /vendor` after the next supervised boot/update.

Note that the usual
"copy-firmware-once" tooling does not cover this device, precisely because
`persist` needs a permanent read-write mount and `super`/`vendor` need live
mapping.

### SELinux runs permissive

Not enforced.

### File capabilities in rootfs archives

The previous release archive omitted `security.capability` xattrs; `rpm -Va`
found ten mismatches, including `/usr/bin/newuidmap`, `/usr/bin/newgidmap`,
`/usr/bin/arping` and `/usr/bin/mtr-packet`. The build now records these xattrs
in the PAX archive. Host-side GNU tar restores them for the legacy SD-card
image path. TWRP's bundled Toybox tar does not restore them, so both TWRP-based
installers now use the installed RPM database's `%{FILECAPS}` metadata and
`setcap` inside the newly extracted Fedora rootfs.

Regression tests cover the build/extraction contracts, the GNU-tar xattr
round-trip, and syntax of the installers. This is fixed in source but still
needs a fresh install followed by `rpm -Va` and a rootless `unshare -r` check on
the tablet; the currently running installation has not been changed by this
source update.

---

## Fixed issues — what they were

### 1 — Battery percentage capped at 96 %

Not a fuel-gauge bug. The SM5714 charger's **float voltage was never
programmed**, so the pack was charged 60 mV short of full. Once programmed, the
tablet reaches 100 %.

### 2 — No palm rejection (S Pen)

The S Pen digitizer and the touchscreen are fully independent input devices.
The touchscreen controller already classifies palm contacts, but the driver
reported every accepted touch type (normal/glove/palm/wet) as a plain finger,
and nothing coordinated the pen's hover state with the touchscreen.

The fix tracks pen proximity and exposes a "should suppress touch" query with a
**250 ms silence timer**, so proximity clears even if the digitizer stops
sending frames when the pen is lifted. The touchscreen calls it at the top of
its handler; while the pen is in range it releases all finger slots and drops
incoming touches. This has to be kernel-level: userspace arbitration can only
reason about touches near the pen's last position, and gets permanently confused
if the pen is not seen at all.

### 3 — Bluetooth lag under 2.4 GHz Wi-Fi

Fixed by substituting Samsung's device-tuned WCN6855 NVM/rampatch for the
generic `linux-firmware` ones, which keeps the BT keyboard and audio lag-free
while 2.4 GHz Wi-Fi is active.

### 5 — GNOME auto-rotation option/integration

The SSC motion sensors and SensorProxy are working and expose orientation data.
The remaining issue is desktop integration: GNOME currently has no auto-rotate
option on this install. The old SensorProxy discovery failure was fixed by the
patched libssc-enabled proxy; do not describe the sensor itself as absent.

Sensor orientation still needs visual validation whenever the GNOME integration
is changed.

### 6 — USB debug link flaky

The RNDIS gadget was converted to ECM.

### 7 — Weak 5 GHz Wi-Fi RX

The ath11k board-data (BDF) file was substituted. Three plausible causes were
ruled out first; the Samsung BDF itself made ath11k worse.

### 9 — Discord/Roblox unreachable (DPI)

The kernel had been built without `nfqueue`, so the DPI-bypass tool had nothing
to work with. Rebuilt with `nfqueue`; both services returned to `200`.

### 10 / 11 — Camera

The front sensor did not probe because the DTS carried the **EEPROM's** I2C
address (`0x20`) instead of the sensor's (`0x21`) — an in-DT address sweep found
it. Once probing, it streams 3408×2556 @ 30 fps. Separately, libcamera had no
sensor helper for this part, so automatic gain control never ran; adding the
helper fixed it.

### 14 — Double tap to turn on the screen

The driver arms the stock gesture (sponge mode, plus two register writes), keeps
the rails on and enables the IRQ as a wake source, reports `KEY_WAKEUP`, and
registers as a wake source. Verified: a suspend entry was ended by a tap after
12.7 s of sleep, while the 150 s RTC fallback never fired.

There is a UI: a GNOME Shell extension settings page toggles it through a
device-control helper, the choice is saved under `/var/lib/gts9wifi/` and
re-applied at boot, and a udev rule makes the touchscreen attribute
group-writable so no privilege prompt is needed. There is deliberately no Quick
Settings toggle.

An earlier "the screen stays dark after a gesture wake" report was **not** a
double-tap defect — it was a broken resume path that restarted nothing.

### 16 — Hardware video decode (iris / VPU 3.0)

The `/dev/video17` node and mainline driver were present, but the image had the
wrong Samsung-signed `vpu30_4v.mbn`: the Azkali X710/X910-family blob has the
same Xtensa loadable segments as X810 CYG1 but a different PAS authentication
tail. On the live SM-X810, the wrong file failed during TrustZone PAS
initialization with `-EINVAL`.

I extracted the exact file from the owner-provided SM-X810 CYG1 stock vendor
image (SHA-256
`c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba`), installed
it on the running Fedora system, and verified a 90-frame H.264 decode through
FFmpeg's `h264_v4l2m2m` wrapper. FFmpeg reported `iris_driver` on `/dev/video17`
in mplane mode. No kernel rebuild or reboot was needed.

The repository owner authorized redistribution of this exact firmware. Its
verified 2.3 MB CYG1 payload is now included in the source tree; the build
checks the SHA-256 and includes it both in fresh root filesystems and the
updater-installable support RPM. A local `GTS9_VPU_MBN` override remains
possible but must match the same hash. This closes the clean-build omission;
it does not by itself prove that Moonlight selected the decoder in a live
stream. Browsers still do not use this stateful V4L2 node; see
[Hardware video decode](Hardware-Notes.md#hardware-video-decode).

### 17 — Speaker volume capped

`linux-firmware` ships **no** CS35L45 blobs at all, so the Cirrus
speaker-protection DSP firmware had never been loaded and the DSP limiter was
not bounding cone excursion. The firmware now ships and loads on all four
amplifiers, so the per-amp volume was raised about 12 dB (380 → 428, that is
−19.25 dB → −7.25 dB, where 457 is 0 dB) and confirmed good by ear.

Two caveats: 7.25 dB is deliberately held back, and the speaker
*characterisation* is still missing — the two coefficient files are not
duplicates, and mainline's `wm_adsp` loads exactly one `.bin` per DSP, so the
separate calibration deploy group is never applied. That is also why the volume
is not simply pushed to 0 dB.

### 18 — `/`, `/etc` and `/usr` owned by the image build user

`systemd-tmpfiles` refuses to canonicalize any path under a non-root-owned
directory, so **every** `tmpfiles.d` entry on this port was inert — and
`systemd-tmpfiles-setup.service` exited 73 on every boot while still reporting
success, because Fedora's unit carries
`SuccessExitStatus=DATAERR CANTCREAT`.

Measured: 125 *unsafe path transition* errors per run, 2,757 wrongly-owned
entries outside `/home`, and 2,429 packaged files flagged by `rpm -Va`
(including `/`, `/etc` and `/usr`).

Two mechanisms, both confirmed by experiment:

- `cp -a src/. dst/` applies the **source** owner to the destination directory
  *itself*, so the overlay copy chowned `/` and `/etc` to the checkout's uid;
- a `usr/` directory entry in the firmware tarball chowns `/usr`, so it took the
  dev host's uid.

The fix restores ownership at the end of staging: rpm's own
`--setugids --setperms` for packaged files, plus a targeted chown of the overlay
and firmware trees — each entry **and every parent directory leading to it**,
because the ancestors are what systemd canonicalizes. It is deliberately **not**
a blanket `chown -R root:root`, which would strip `root:systemd-journal`,
`apache:apache`, `abrt:abrt` and similar legitimate service-account ownership.

Verified on the tablet: tmpfiles exit 73 → 0, unsafe-path errors 125 → 0,
`rpm -Va` owner/group mismatches 2,429 → 2.

Note that `rpm -a --setugids --setperms` exits 255 with a handful of
*restored failed* errors, because some packages list `__pycache__/*.pyc` files
that do not exist in the built image. This is benign — rpm still restores
everything it can — so the build treats a non-zero exit as a note rather than an
error.

### 20 — Wi-Fi dead on the 7.2.1–7.2.6 stable kernels

On every 7.2.1+ kernel the WCN6855 loses MHI power-up deterministically:
the link trains and the chip identifies (`wcn6855 hw2.1`), then MHI dies
waiting for the device (`did not load image over BHI, -5` / `did not enter
READY state`, -110) — on cold boot, warm boot and PCI rescan alike, with
Bluetooth on the same module unaffected.  Plain 7.2.0 boots Wi-Fi in under a
second with the identical DTS, patches, config and firmware, so the port
bases on 7.2.0 (`kernel-7.2.0-gts9wifi-1`) until the culprit is named.
Ruled out on hardware: the posted-write flush fixes (reverted in a test
kernel — no change), ASPM (disabled — failure persists), the pipe-mux
unpark sentinel, the pwrseq/pwrctrl DT wiring.  The regression is isolated
to the 7.2.1–7.2.6 stable deltas.

### 19 — Kernel log flooded by ADSP handover messages

`Handover signaled, but it already happened` repeated at roughly **5.4 times
per second** — 99.98 % of the `dmesg` ring (37,402 of 37,411 lines), evicting
real diagnostics within seconds. The handover interrupt is level-triggered and
the remote keeps it asserted after the first handover, so the repeat is
expected and harmless. Fixed by logging it at debug level
(`quiet-adsp-handover-already-happened.patch`); no behaviour change.
