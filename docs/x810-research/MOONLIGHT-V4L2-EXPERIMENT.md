# Moonlight V4L2 decode experiment (X810)

**Status: opt-in launcher prepared; Moonlight streaming decode has not been
tested on the tablet.** The hardware decoder and its firmware were verified
independently with synthetic FFmpeg input, but that does not prove Moonlight
uses them for a live stream. Do not call Moonlight hardware decoding working
until a real stream confirms it.

## Current evidence

Read-only checks on the SM-X810 found `/dev/video17` as the Iris stateful
decoder with H.264, HEVC, VP9, and AV1 formats. The current tablet state has
no installed `com.moonlight_stream.Moonlight` Flatpak and only the Fedora
Flatpak remote configured, so the desktop launcher currently cannot start the
app. Earlier research examined Moonlight Flatpak `6.1.0` stable/aarch64; its
matching Flathub manifest pins Moonlight `v6.1.0` and enables FFmpeg's
`h264_v4l2m2m` and `hevc_v4l2m2m` decoders. The Flathub manifest grants `--device=all`, which exposes the V4L2 character
  devices to Moonlight. The X810 launcher relies on this declared permission
  and does not add a broader runtime permission or persistent override.
- On this tablet, `/dev/video17` identifies as the `qcom-iris-decoder` and is
  a **stateful** V4L2 M2M multi-planar decoder; format enumeration exposes
  H.264, HEVC, VP9, and AV1. `/dev/video18` is the encoder node, not a decode
  node, and has not been tested.
- `/usr/lib/firmware/qcom/vpu/vpu30_4v.mbn` has the owner-supplied X810 CYG1
  SHA-256 `c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba`.
- The owner previously verified synthetic H.264 decode with FFmpeg's V4L2 M2M
  wrapper. That validates the VPU/firmware path, **not Moonlight integration**.

Moonlight v6.1.0 source explicitly reads `H264_DECODER_HINT` and
`HEVC_DECODER_HINT` and tries the named FFmpeg decoder before automatic
selection. The Flathub manifest for that release builds both V4L2 M2M FFmpeg
decoders and grants device access. These are supported integration points, not
evidence that a stream is decoded in hardware. A hint chooses the FFmpeg
decoder implementation; it does not bind a specific `/dev/videoN` node or
confirm the codec context initialized. Moonlight logs
`Using custom H.264 decoder (H264_DECODER_HINT): h264_v4l2m2m` on successful
hinted initialization, and an error on failure; the stream should still be
checked for actual V4L2 device use and decoded frames. This is also why the
launcher names the API rather than claiming to select `qcom-iris` directly.
Moonlight v6.1.0 contains an upstream fix for V4L2 M2M test-frame
initialization: the decoder context dimensions are set to match its 720p
capability-test frame. Upstream identified dimension mismatch as the cause of
Qualcomm Venus capture-buffer allocation failures (and a related V4L2
interoperability bug); the fix landed in
[`1dd6cdb`](https://github.com/moonlight-stream/moonlight-qt/commit/1dd6cdb567d9c79bcbd8caee13d999a447a8b413)
and is present in the pinned v6.1.0 source. X810 uses `qcom-iris`, not Venus,
so this is relevant stateful-M2M precedent, not proof of X810 support. See
[Moonlight v6.1.0 FFmpeg integration](https://github.com/moonlight-stream/moonlight-qt/blob/v6.1.0/app/streaming/video/ffmpeg.cpp),
[Flathub's pinned FFmpeg source](https://github.com/cgutman/FFmpeg/commit/d17de7e33f1332cc2fb3f5afab9ed4f29699c5a0),
[Flathub manifest](https://github.com/flathub/com.moonlight_stream.Moonlight/blob/master/com.moonlight_stream.Moonlight.json),
[Flatpak device permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html#device-access),
and the [Moonlight hardware-decoding guide](https://github.com/moonlight-stream/moonlight-docs/wiki/Fixing-Hardware-Decoding-Problems).

This is **not VA-API**. VA-API is a separate userspace API; X810's Iris block
is exposed here through the V4L2 M2M stateful interface, and no VA-API driver
for it is known. `/dev/video17` is the decoder; `/dev/video18` is an encoder.
Neither a `/dev/dri` render node nor the fact that Moonlight renders via GPU
proves video decode acceleration. Do not infer browser VA-API support from
this launcher or decoder availability.

The distinction matters for Moonlight's codec reporting: `h264_v4l2m2m` is an
FFmpeg stateful V4L2 wrapper, not a VA-API or FFmpeg hardware-device decoder.
Moonlight provides an explicit decoder-hint path for this kind of decoder.
The pinned Flathub FFmpeg source (`d17de7e33f1332cc2fb3f5afab9ed4f29699c5a0`)
sets `AV_CODEC_CAP_HARDWARE` on its V4L2 M2M decoder definitions, and Moonlight
v6.1.0's `isHardwareAccelerated()` checks that flag. Therefore a successfully
initialized `h264_v4l2m2m`/`hevc_v4l2m2m` decoder should be reported as hardware
and should be eligible for **Force hardware**. The earlier claim that Moonlight
would generically classify these wrappers as software was incorrect. If the
hardware option is absent or the probe warns, diagnose decoder initialization,
device access, firmware, and renderer compatibility; the hint only selects the
decoder and does not fix initialization. Keep **Automatic** for the initial
live-stream test until those paths are confirmed.

The repository owner authorized redistribution of the exact X810 CYG1 VPU
firmware. It is now included in clean rootfs builds and the updater-installable
support RPM, with its hash checked during staging. A local `GTS9_VPU_MBN`
override must match that same image. The launcher itself only supplies the
decoder hints; it does not install firmware or change the kernel.

## Optional install/update flow

The Fedora rootfs does not enable Flathub or install Moonlight. To explicitly
configure the system Flathub remote and install/update the system-wide app used
by the X810 launcher, run:

```sh
sudo x810-moonlight-install
```

The command adds the official Flathub remote only if absent, then runs Flatpak's
`--or-update` operation for `com.moonlight_stream.Moonlight`. It requires an
internet connection and administrator privileges. Re-run it when you want to
update Moonlight. This is separate from normal port/rootfs updates and does not
launch Moonlight. Once installed, **Moonlight (X810 V4L2 decode)** is an
opt-in launcher; it supplies decoder hints but still requires live-stream
validation before claiming hardware decode works.

## Opt-in launcher

The Fedora overlay adds a separate **Moonlight (X810 V4L2 decode)** app-grid
entry and `/usr/bin/x810-moonlight-v4l2`. It starts only the system-installed
Moonlight Flatpak and sets these per-launch decoder hints:

```text
H264_DECODER_HINT=h264_v4l2m2m
HEVC_DECODER_HINT=hevc_v4l2m2m
```

The wrapper relies on the Flathub manifest's declared device permission
instead of adding a redundant `--device=all` run flag or persistent override.
This does not add or broaden access beyond the installed app's manifest. The normal
Moonlight launcher and other applications are unchanged. The wrapper does not
set `DRM_FORCE_DIRECT`; that is a Raspberry Pi/KMS-specific setting and is not
assumed appropriate for GNOME Wayland. If the opt-in path fails, preserve the
Moonlight log rather than adding sandbox or DRM overrides blindly.

## Owner-run validation still required

When the tablet is available, first confirm `/dev/video17` can be opened by the
desktop user and the exact X810 CYG1 firmware is present. Then stream H.264
using the opt-in launcher. Inspect Moonlight's log for its
`H264_DECODER_HINT` selection and successful frame decode. The expected
`Using custom H.264 decoder ...` line confirms hint initialization, not by
itself that the VPU is processing the live stream. Also verify the opened
V4L2 node is `/dev/video17` (or use a device-use counter), and compare CPU time
with a software-decoding run. A picture, a listed FFmpeg decoder, or an open
file alone is not sufficient proof. Test HEVC separately after H.264. Record
the negotiated codec, resolution, frame rate, decoder, and dropped-frame/CPU
measurements. Until then, live Moonlight stream decode remains **unverified**.
