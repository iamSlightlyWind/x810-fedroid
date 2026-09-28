# X810 haptics

The board DTS describes a `gpio-vibrator` on active-high TLMM GPIO18 and has no
`vcc-supply`. Linux's generic `gpio-vibra` driver previously treated that
regulator as mandatory, so its probe failed before it could register the
force-feedback input device.

The X810 kernel patch makes the regulator optional only when the device has no
`vcc-supply` property. A declared rail with a real lookup/enable error still
fails normally. The kernel RPM checks that the haptics driver and X810 DTB are
present, then uses `fdtget` to verify that the compiled DTB still points to
active-high GPIO18 on the SM8550 TLMM controller. CI also runs the source
contract test; compilation remains part of the kernel RPM build.

The latest standalone kernel release
[`x810-kernel-4b02b0c61f8e6090`](https://github.com/iamSlightlyWind/x810-fedroid/releases/tag/x810-kernel-4b02b0c61f8e6090)
includes the compiled kernel/DTB and RPM from source commit
`562374ff64ce7e9ea8ae844768bd548cb88c4099`. Tab Companion build #19 also
packages its haptics service and UI. These artifact checks do not prove that
the tablet runs that boot set or that the motor works. After installation,
confirm that the vibrator input device registers and that a short force-feedback
test actually vibrates the tablet; a successful build or DTB check alone is
not proof of motor operation.
