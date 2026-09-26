# Tab S9+ PipeWire audio

## Working fix (Fedora on X810)

ALSA playback worked, but PipeWire application streams were left unlinked: the
speaker node had no ports and `/proc/asound/card0/pcm0p/sub0/status` stayed
`closed`. A controlled A/B test identified WirePlumber's V4L2 monitor as the
trigger. With only `monitor.v4l2` disabled (the `libcamera` monitor and normal
`main` policy retained), the sink and playback streams gained ports, PulseAudio
clients linked to both speaker channels, and the ALSA PCM entered `RUNNING`.
Re-enabling V4L2 while disabling `libcamera` reproduced the failure.

The tablet's persistent user config is:

- `~/.config/wireplumber/wireplumber.conf.d/51-tablet-audio.conf`: adds the
  `tablet-audio` profile inheriting `main`, with `monitor.v4l2 = disabled`.
- `~/.config/systemd/user/wireplumber.service.d/51-tablet-audio.conf`: starts
  WirePlumber with `--profile=tablet-audio`.
- `51-disable-compr-offload.conf` disables the duplicate `alsa_card.comprC0`
  presentation of the same ALSA card; the normal UCM card remains enabled.

Validated with a low-level PulseAudio-compatibility test (`gst-launch-1.0`
`pulsesink`): stream showed active FL/FR links to the built-in speakers and
ALSA PCM was `RUNNING`. The speaker gain was 2% during tests and restored to
40% afterwards.

Tradeoff: V4L2 camera capture is disabled in this profile; Bluetooth and
libcamera remain enabled. If camera capture is needed, test its specific
monitor separately before re-enabling V4L2, since doing so reproduced the
audio-routing failure.
