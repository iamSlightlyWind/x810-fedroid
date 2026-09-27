# libcamera-hi1337

Patch adding a libcamera `CameraSensorHelper` for the SK Hynix HI-1337 as
found on the Galaxy Tab S9 Wi-Fi (`gts9wifi`), where the port registers the
sensor as **`hi1337-gts9u`**.

## Scope

This helper affects AGC and black-level correction only after the HI1337
camera has been enumerated and libcamera has selected the simple software IPA.
It does not fix sensor enumeration, portal/preview connection failures, or
camera permissions.

## Why

libcamera's software ISP refuses to create a sensor helper for an unknown
model:

```
IPASoft: Failed to create camera sensor helper for hi1337-gts9u
```

The helper is what converts between real analogue gain and the sensor's gain
register, and it is what supplies the sensor's black level. Without it the
IPA's AGC is inert — the sensor simply keeps whatever analogue gain it was
left at, in practice the **maximum (240 = 16×)**, giving a violently noisy
image — and the black-level correction is skipped, which shows as a **heavy
green cast** in the processed output.

Adding the helper fixes both: AGC actively meters and adjusts again, and the
processed image is correctly coloured.

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
continue replacing the vendor module safely.

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

On a Galaxy Tab S9 Wi-Fi, while streaming through libcamera, the sensor's
`analogue_gain` now moves (`64 → 89 → 135 → 204 → 240`) as AGC meters,
where previously it never changed. A processed frame captured through
`libcamerasrc` shows natural colour instead of the previous green cast.

## Known limitation

This unlocks **AGC and correct colour**, but *not* focus control: libcamera
0.7.1's simple pipeline handler has no lens support at all (no `CameraLens`,
no `LensPosition` control), so no sensor helper can expose it. Driving the
`dw9808` VCM still requires direct V4L2 control of the lens subdev — see
docs/Hardware-Notes.md, Cameras.
