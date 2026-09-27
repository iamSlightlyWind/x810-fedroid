# Tab S9+ PipeWire audio

## Working fix (Fedora on X810)

ALSA playback worked, but PipeWire application streams were left unlinked: the
speaker node had no ports and `/proc/asound/card0/pcm0p/sub0/status` stayed
`closed`. A controlled A/B test identified WirePlumber's V4L2 monitor as the
trigger. With only `monitor.v4l2` disabled (the `libcamera` monitor and normal
`main` policy retained), the sink and playback streams gained ports, PulseAudio
clients linked to both speaker channels, and the ALSA PCM entered `RUNNING`.
Re-enabling V4L2 while disabling `libcamera` reproduced the failure.

The reproducible port fix is now shipped as a WirePlumber system configuration
fragment at
`/usr/share/wireplumber/wireplumber.conf.d/90-x810-audio-routing.conf`:

```ini
wireplumber.profiles = {
  main = {
    monitor.v4l2 = disabled
  }
}
```

WirePlumber's documented profile override keeps the standard `main` profile,
including ALSA audio, Bluetooth and policy, while disabling only the generic
V4L2 camera monitor. Its separate `monitor.libcamera` stays enabled. An ALSA
monitor rule also hides the duplicate `alsa_card.comprC0` device while keeping
the normal UCM-backed ALSA card. This is delivered by the Fedora image and the
updater-installable `x810-fedora-port` RPM, so it applies to new installs and
updated systems instead of depending on one user's home directory.

The live tablet was originally tested with this equivalent per-user setup:

- `~/.config/wireplumber/wireplumber.conf.d/51-tablet-audio.conf`: added the
  `tablet-audio` profile inheriting `main`, with `monitor.v4l2 = disabled`.
- `~/.config/systemd/user/wireplumber.service.d/51-tablet-audio.conf`: started
  WirePlumber with `--profile=tablet-audio`.

Validated with a low-level PulseAudio-compatibility test (`gst-launch-1.0`
`pulsesink`): stream showed active FL/FR links to the built-in speakers and
ALSA PCM was `RUNNING`. The speaker gain was 2% during tests and restored to
40% afterwards.

Tradeoff: the generic PipeWire V4L2 capture monitor is disabled. The tablet's
CSI cameras are exposed through the separately enabled libcamera monitor, and
the V4L2 devices remain available to applications that open them directly.
USB UVC cameras exposed only through PipeWire's V4L2 monitor will not appear as
PipeWire camera sources. Re-enable that monitor only after reproducing the
audio routing test, since doing so reproduced the failure on this tablet.
