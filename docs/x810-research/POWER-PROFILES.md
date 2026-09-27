# X810 CPU power profiles

The Fedora 44 image currently has neither `power-profiles-daemon` nor TuneD
installed. GNOME therefore has no power-profile service, even though the
running `7.2.0-gts9wifi` kernel exposes the CPUFreq governors `schedutil`,
`ondemand`, `userspace`, and `performance` on all three policies.

The port now includes Fedora's `tuned-ppd` package for GNOME's standard
PowerProfiles D-Bus API. Its Fedora 44 mapping keeps `balanced` as the default,
maps `power-saver` to TuneD's `powersave` profile, and `performance` to
`throughput-performance`. Those profiles select governors supported by the
current X810 CPUFreq policies: `schedutil` for balanced/power-saver and
`performance` for the performance profile. The updater RPM enables the
services so already-installed systems get the same UI as new rootfs images.

This is a CPUFreq-level switcher, not a Samsung power HAL or complete SoC
power-management port. This tablet has no ACPI platform-profile interface;
TuneD settings for other hardware may therefore be no-ops. Performance mode
can raise power draw and heat. The source change has not yet been installed on
the tablet; verify the GNOME selector, active profile, and governors after the
next support-package update. Restore `balanced` if thermal or battery behavior
is undesirable.
