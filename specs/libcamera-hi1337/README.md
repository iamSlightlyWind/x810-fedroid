# libcamera-hi1337

Patch adding a libcamera `CameraSensorHelper` for the SK Hynix HI-1337 as
found on the Galaxy Tab S9 Wi-Fi (`gts9wifi`), where the port registers the
sensor as **`hi1337-gts9u`**.

## Scope

The helper and its matching IPA tuning profile affect gain/black-level and
processed-image behavior only after the HI1337 camera has been enumerated and
libcamera has selected the simple software IPA. The port-specific
`hi1337-gts9u.yaml` profile is installed at libcamera's standard
`/usr/share/libcamera/ipa/simple/` path. Without it, libcamera falls back to
`uncalibrated.yaml`; the runtime warning for that fallback is what this profile
addresses.

This does **not** fix intermittent sensor enumeration, PipeWire portal or
preview-target failures, missing DMA-BUF providers/software-ISP initialization,
or camera permissions. It also does not add autofocus.

## Why

libcamera's software ISP refuses to create a sensor helper for an unknown
model:

```
IPASoft: Failed to create camera sensor helper for hi1337-gts9u
```

The helper converts between real analogue gain and the sensor's gain register,
and supplies its black level. The camera-specific tuning profile configures
the simple IPA's black-level, auto-white-balance, colour-correction and
auto-gain algorithms. The live X810 inspection found the helper loading but
the profile missing, so libcamera selected the generic uncalibrated fallback.
Shipping the matching profile fixes that specific missing-tuning fallback; it
does not make a camera node appear or repair a broken PipeWire stream.

The profile used here is the CC0-1.0 HI1337 configuration from
`agcarbajo/ubuntu-galaxy-tab-s9-ultra`. It is reference tuning, not factory
calibration for the X810.

## Values used

| Item | Value | Rationale |
|---|---|---|
| Gain model | `AnalogueGainLinear{ 1, 16, 0, 16 }` | Register `0x0213` holds codes 0..240 encoding `gain = 1 + code / 16`, i.e. 1×..16× — the same scheme libcamera already uses for the Hynix HM-1246 |
| Black level | `4096` | Pedestal 64 at 10 bits, scaled to 16-bit as `blackLevel_` requires (same convention as the IMX219 entry) |

## Building and deploying

The Fedora 44 support build compiles this helper against the exact pinned
Fedora 44 libcamera source package and installs it in the separate
`/usr/lib64/libcamera/ipa-x810/` directory. It does **not** replace Fedora's
`/usr/lib64/libcamera/ipa/ipa_soft_simple.so`, so normal Fedora updates can
continue replacing the vendor module safely. The CC0 tuning YAML is shipped
under `/usr/share/libcamera/ipa/simple/` by the same overlay, so both a fresh
rootfs and the cumulative support-RPM update receive it.

For a local developer build, use the repo's native Fedora 44/aarch64 build
container and run `tools/build-libcamera-hi1337-ipa.sh STAGING_ROOT`. The
script fetches and checksum-verifies Fedora's pinned source RPM, applies the
Fedora and X810 patches, builds the matching soft IPA, and stages the plugin
plus its applicable license notices. It intentionally omits the locally
produced `.sign`: Fedora's stock IPA is signed, while an untrusted custom IPA
is loaded by libcamera in its isolated `soft_ipa_proxy` worker rather than
being treated as an in-process signed module.

The port ships `/etc/libcamera/configuration.yaml` to prefer the X810 module
path and an environment setting for applications that honor
`LIBCAMERA_IPA_MODULE_PATH`. Existing desktop sessions may need a fresh login
to pick up the environment setting. Do not copy the custom plugin over
Fedora's system IPA file.

The RPM and pinned source make the *inputs and target ABI* repeatable, but the
builder image and Fedora toolchain package set are not yet digest-locked, so
this is not a claim of bit-for-bit reproducibility.

## Verified

An earlier X810 stream test of the helper recorded `analogue_gain` changes
(`64 → 89 → 135 → 204 → 240`) as the scene was metered. In the current
read-only audit, `cam -l` loaded the custom helper and enumerated both cameras,
but logged that `hi1337-gts9u.yaml` was missing and fell back to
`uncalibrated.yaml`. This change packages the reference profile; no new frame
was captured to validate its image quality on the current installation.

## Known limitation

Focus is separate: Fedora's libcamera 0.7.1 simple pipeline handler has no
lens support (`CameraLens` / `LensPosition`), so neither the sensor helper nor
this tuning YAML adds autofocus. The port uses a fixed V4L2 lens position via
udev; driving the `dw9808` VCM still requires direct V4L2 control of the lens
subdevice — see `docs/Hardware-Notes.md`, Cameras.
