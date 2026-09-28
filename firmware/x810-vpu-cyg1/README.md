# SM-X810 CYG1 VPU firmware

`vpu30_4v.mbn` is the exact Samsung PAS-authenticated image required by the
SM-X810's Qualcomm iris VPU. It is extracted from the owner's Samsung XSP
`X810XXS5CYG1` firmware. The repository owner confirmed redistribution rights
for this exact file. The X810 Fedora port documents its runtime integration in
`docs/x810-research/MOONLIGHT-V4L2-EXPERIMENT.md`.

SHA-256: `c02a4f1cb253f4b817994c00145dc9abbd59a10bfcd9fd5d0c2f223c0dc543ba`.
The rootfs and support-RPM builders verify this hash before packaging. A
similarly named X710/X910 firmware has a different PAS certificate tail and is
rejected by the X810 secure loader; never substitute it.
