# SM-X810 CYG1 Adreno 740 firmware

This directory contains the six CYG1 firmware files required by the SM-X810
GPU secure loader. The Fedora generic SM8550 files with the same names are not
compatible with this device's signed ZAP firmware.

The repository owner confirmed redistribution rights for these exact files.
`tools/x810-gpu-firmware.py` checks each SHA-256 before staging them into the
Fedora root filesystem and `vendor_boot` initramfs. The kernel and rootfs
fingerprints include this directory, so changing any payload rebuilds both
dependent components. Do not replace files from another model or firmware
revision without updating the reviewed hashes and validating on the X810.

The binary payload is from Samsung's SM-X810 XSP firmware release
`X810XXS5CYG1`; the source repository pins the expected hashes in the staging
helper rather than storing a separate generated manifest here.
