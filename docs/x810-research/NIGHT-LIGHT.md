# GNOME Night Light on the X810

The SM-X810 DSI panel has no EDID. Mutter 50.5 exposed the DPU's 1024-entry
`GAMMA_LUT` CRTC property and reported Night Light supported, but colord had no
assigned profile for `xrandr-DSI-1`. With no profile, Mutter's color-device
white-point update returned without generating a LUT: GNOME reported Night
Light active while the DRM `GAMMA_LUT` blob stayed unset.

The support RPM now installs and enables `gts9wifi-color-profile.service`. It
creates the persistent system-scope `xrandr-DSI-1` display record and assigns
Fedora's standard sRGB profile only when no profile is already assigned. Thus
the generated per-session Mutter device inherits sRGB, while an owner's
calibrated/custom profile is left alone. This supplies a generic color-space
fallback, **not** a measured panel calibration.

Validated live on Fedora 44 / kernel `7.2.0-gts9wifi`: after assigning the
system profile and enabling Night Light at 2700 K, the active DSI CRTC's
`GAMMA_LUT` property changed from `0` to a blob ID (`110`), confirming the
hardware LUT is actually being programmed. No reboot or GDM restart was
needed. The user-facing visual check was also confirmed. The support RPM makes
the setup reproducible on fresh installs and upgrades.
