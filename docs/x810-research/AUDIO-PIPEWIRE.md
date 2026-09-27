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

## Read-only regression check (no test tone)

The device's current session was audited over SSH without starting playback or
restarting audio services. PipeWire, WirePlumber, and `pipewire-pulse` were
active; the SM-X810 ALSA card, its UCM-backed stereo speaker sink and stereo
microphone source were visible; and application stream ports were linked to
both speaker channels. While an existing Firefox stream held the PCM open,
`/proc/asound/card0/pcm0p/sub0/status` was `RUNNING`. The live machine still
uses its old per-user `tablet-audio` profile; the system-wide fragment above
is not installed there yet, so this confirms the working per-user baseline,
not yet a clean-install/update test of the packaged fragment.

These commands inspect the graph without producing sound:

```sh
systemctl --user is-active pipewire wireplumber pipewire-pulse
wpctl status -n
pw-link -l
cat /proc/asound/card0/pcm0p/sub0/status
```

The PCM may be `closed` when no application is actively holding it; that by
itself is not a failure. The useful static regression signals are that the
UCM speaker sink is present with stereo ports and that PipeWire application
streams, when present, have FL/FR links to those ports. Do not use `speaker-test`,
GNOME's left/right test buttons, or another playback command for this check.

## GNOME Settings output-test hang

This is separate from the previously diagnosed missing PipeWire ports. On the
latest read-only inspection, GNOME Settings had no recorded coredump. Kernel
logs show the sound card appearing after deferred ADSP/DPCM probe, plus one
`MultiMedia2 Playback` message saying no backend DAI is enabled. The packaged
UCM intentionally routes the speaker through MultiMedia1 (`hw:...,0`) to
PRIMARY_MI2S_RX and exposes MultiMedia3 capture (`hw:...,2`); its mixer leaves
the MultiMedia2-to-PRIMARY_MI2S_RX route off. The normal PipeWire speaker sink
is bound to PCM0, and existing streams link to it. Therefore the log does not
prove that enabling MultiMedia2 is correct: changing that route blindly could
create a second path to the same amplifiers. Keep MultiMedia2 untouched until a
reproduction captures the GNOME/PipeWire stream's selected node and the
corresponding ALSA PCM state. No GNOME test button or audio tone was triggered
during this audit.
