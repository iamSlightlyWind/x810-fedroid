# X810 CPU power profiles

Fedora's `tuned-ppd` is the GNOME Power Profiles D-Bus implementation. The
support RPM enables it, and the tablet's balanced profile was already active.
That was not enough: Linux's `qcom-cpufreq-hw` driver deferred because the
SM8550 OSM L3 interconnect provider (`icc_osm_l3`) was present as a module but
was never loaded. The result was no `/sys/devices/system/cpu/cpufreq/policy*`
and therefore no effective governor control.

On the running SM-X810, manually loading `icc_osm_l3` created all three CPU
frequency policies (`policy0`, `policy3`, `policy7`) with `schedutil`,
`performance`, `ondemand`, and `conservative` governors. A short D-Bus profile
test switched all three policies to `performance` and restored them to
`schedutil` under `balanced`. The support RPM now loads the provider immediately
when upgraded, and `/etc/modules-load.d/x810-cpufreq.conf` ensures it is loaded
on subsequent boots. The kernel module's BTF metadata warning remains, but the
module loaded and both CPUFreq policy creation and TuneD profile changes worked.

There was a second, subtler problem: TuneD's generic `powersave` profile lists
`schedutil` before `powersave` as its preferred governor. On this tablet,
`schedutil` is available, so selecting GNOME's Power Saver can leave the CPU
free to boost under load; a CPU benchmark can therefore score like Balanced
while the tablet heats up. The port now adds an `x810-power-saver` profile
which inherits the generic power-saving tunables but explicitly selects the
CPUFreq `powersave` governor, and maps the standard PPD `power-saver` profile
to it. The support RPM migrates the mapping idempotently, backs up the original
PPD config once, and restarts `tuned-ppd` only if the mapping changed.

This is CPUFreq-level control, not a Samsung power HAL or a complete SoC
performance policy. It does not establish GPU boost behavior or thermal limits.
The X810-specific saver mapping is source-tested but still needs validation on
the tablet: confirm all three CPU policies report `powersave` in Power Saver,
`schedutil` in Balanced, and `performance` in Performance, then compare
frequency/temperature under the same benchmark. Use Balanced by default;
Performance is expected to use more power and produce more heat.
