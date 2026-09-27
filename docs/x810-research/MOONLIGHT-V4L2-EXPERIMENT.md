# Moonlight V4L2 decode experiment (X810)

**Status: launcher prepared; Moonlight streaming decode has not been tested on
the tablet.** Do not describe Moonlight hardware decoding as working until the
owner runs a real stream and confirms the selected decoder and device.

## Current evidence

Read-only checks on the SM-X810 found:

- Moonlight Flatpak `6.1.0`, stable, aarch64, installed system-wide.
- The Flatpak's FFmpeg lists `h264_v4l2m2m` and `hevc_v4l2m2m` decoders.
- `/dev/video17` identifies as `qcom-iris-decoder`; format enumeration exposes
  compressed H.264, HEVC, VP9, and AV1 formats.
- `/usr/lib/firmware/qcom/vpu/vpu30_4v.mbn` has the owner-supplied X810 CYG1
  SHA-256 `c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba`.
- The owner previously verified synthetic H.264 decode with FFmpeg's V4L2 M2M
  wrapper. That validates the VPU/firmware path, **not Moonlight integration**.

Moonlight upstream includes H.264/HEVC V4L2 M2M decoder handling and decoder
hints; Flathub's current manifest builds those FFmpeg decoders. These are
candidate integration points, not evidence that a Moonlight stream decodes in
hardware. See the [Moonlight-Qt FFmpeg integration](https://github.com/moonlight-stream/moonlight-qt/blob/master/app/streaming/video/ffmpeg.cpp),
[Flathub build manifest](https://github.com/flathub/com.moonlight_stream.Moonlight/blob/master/com.moonlight_stream.Moonlight.json),
and [Moonlight hardware-decoding guide](https://github.com/moonlight-stream/moonlight-docs/wiki/Fixing-Hardware-Decoding-Problems).

The public Fedora release does not redistribute the proprietary VPU firmware.
A clean install needs the owner's X810 CYG1 firmware staged by the documented
`GTS9_VPU_MBN` local-build path before this hardware-decoding experiment can
work. The launcher neither installs firmware nor changes the kernel.

## Opt-in launcher

The Fedora overlay adds a separate **Moonlight (X810 V4L2 decode)** app-grid
entry and `/usr/bin/x810-moonlight-v4l2`. It starts only the system-installed
Moonlight Flatpak with these per-launch environment variables:

```text
H264_DECODER_HINT=h264_v4l2m2m
HEVC_DECODER_HINT=hevc_v4l2m2m
```

No persistent Flatpak override is created; the normal Moonlight launcher and
other applications are unchanged. The wrapper intentionally does not set
`DRM_FORCE_DIRECT`, which is not assumed appropriate for GNOME Wayland. If the
opt-in path fails, use the original Moonlight launcher.

## Owner-run validation still required

When the tablet is available, test a Moonlight stream with H.264 selected on
the host first. Inspect Moonlight's own log for `h264_v4l2m2m`, the `qcom-iris`
decoder, and `/dev/video17`; also compare CPU use to a software-decoding run.
Do not treat a visible picture or the presence of a decoder in `ffmpeg -decoders`
as proof of hardware decode. Test HEVC separately only after H.264 is confirmed.
Until those checks pass, mark Moonlight hardware decoding **unverified**.

The tablet currently exposes an ALSA `MultiMedia3 Capture` PCM and a
PipeWire `HiFi__Mic__source` node. The PCM was idle/closed during read-only
inspection. No microphone capture was made, so recording quality and application
capture remain unqualified.
