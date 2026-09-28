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

This is a source-side probe fix, not yet a device-confirmed haptics fix. After
installing the newly built kernel through the usual recovery workflow, confirm
that the vibrator input device registers and that a short force-feedback test
actually vibrates the tablet. Do not treat a successful kernel build or DTB
check as proof of motor operation.
