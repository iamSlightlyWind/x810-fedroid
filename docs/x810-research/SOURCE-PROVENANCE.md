# Source provenance

Retrieved 2026-09-24; complete Git histories were cloned locally under the
git-ignored `work/upstream/`. These are research inputs only; do not edit or
copy code from them without recording provenance and license.

| Repository | Branch | Commit | Purpose |
|---|---|---|---|
| `nacht20-de/gts9wifi-fedora-linux` | `main` | `ab123e7d1dbc0cbcd35661f9761197e977b15aa9` | Main Fedora prior art, X710 kernel/DTS, scripts, firmware staging, issue history |
| `agcarbajo/ubuntu-galaxy-tab-s9-ultra` | `main` | `4ff9d4b0ba1ae40e7605ad54c0ffe561c1e26a60` | X910 internal-UFS dual boot, boot chain, switchers, safety and current hardware findings |
| `agcarbajo/postmarketos-galaxy-tab-s9-ultra` | `master` | `b1dcca03fdc7952de60c3e9ab49e860b6bd45e8c` | Historical X910 bring-up and upstream audit |
| `samsung-sm8550/android_kernel_samsung_sm8550-devicetrees` | `lineage-22.2` | `704fadc17de7e4999fa47aef6ef3046385cb0ec4` | Search candidate for downstream DT; this snapshot contains common Qualcomm/Samsung DT sources but no `gts9p`/`gts9pwifi` board DTS and is not treated as exact-X810 evidence |

## Mainline kernel baseline

The active X710 Fedora port pins its known-tested baseline to Linux 7.2.0. Its
maintainer's issue register documents deterministic WCN6855 MHI/WLAN failure
on 7.2.1 through 7.2.6. The kernel.org index lists 7.2.7 as the current stable
release as of 2026-09-24, but the inspected X710 source does not establish
whether 7.2.7 fixes that device regression; do not assume either result.
Start X810 bring-up on 7.2.0 to avoid adding an unmeasured kernel delta.

| Source | Revision / hash | Verification |
|---|---|---|
| [kernel.org Linux stable 7.2.0 tarball](https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.tar.xz) | SHA256 `f9fef3d14c0df53819026f4be74459835c2a0b0dcbf5b5bbd9ea19f0829402b3` | Detached `linux-7.2.tar.sign` verified successfully; signing-key fingerprint `647F 2865 4894 E3BD 4571 99BE 38DB BDC8 6092 693E`, matching kernel.org's published Greg Kroah-Hartman fingerprint. Local extracted/prepared copy is ignored under `work/kernel-source/`. |

## Exact-build inputs obtained 2026-09-24

- Firmware bundle: `SM-X810`, XSP, `X810XXS5CYG1`, CSC `X810OXM5CYG1`.
  Downloaded to the user's Downloads directory; outer ZIP MD5
  `e995aca49b2e17c51cdcfdc0866b297d` matches the indexed checksum. AP and BL
  payloads were extracted read-only under ignored `work/firmware-cyg1/` and
  compared against the live partition captures. The original download is not
  copied into Git.
- Samsung source: inner archive `SM-X810_15_Opensource.zip` inside `Kernel.zip`.
  Its SHA256 is `bad7b14888697a4b5c553dd621ee61d1509ef46eff0ebe098a3de7a5b10b0354`;
  the containing `Kernel.zip` SHA256 is
  `bb4c140b08bbc7877408f08c02ae41c27ab03d432e89ed684f36971187e6ab28`.
  The package has no Git commit metadata. README target is
  `gts9pwifi_eur_open`; downstream common kernel is 5.15.153. Board files are
  in `kernel_platform/msm-kernel/arch/arm64/boot/dts/samsung/galaxytab/gts9pwifi/`
  for overlays `w00_r00`, `w00_r02`, and `w00_r04`. Exact panel source
  `GTS9P_ANA38407_AMSA24VU05` is included. Full extracted source remains local
  and git-ignored; do not commit proprietary firmware/source archive contents.

### CYG1 vs live boot partition comparison

The stock AP `vendor_boot.img` and `dtbo.img` are byte-identical to the live
partition captures. Stock AP `boot.img`, `init_boot.img`, and `recovery.img`
are not byte-identical. CYG1 BL `vbmeta.img` is signed (`SHA256_RSA4096`,
flags 0, descriptors), while the live vbmeta capture is algorithm `NONE`,
flags 2, no descriptors. This proves the current boot set differs from stock
CYG1; the exact cause of each difference is not yet attributed. Note that
`vbmeta_system.img` in AP is not the `vbmeta` image from BL.

The owner confirms that the TWRP installation instructions required modified
`vbmeta`. This makes the live vbmeta difference expected in context, although
the exact guide revision and whether its distributed image matches the capture
have not been checked yet. KernelSU/TWRP are owner-confirmed current customizations;
the exact image-level changes to `boot` and `init_boot` remain unclassified.

The old `nacht20-de/gts9wifi-fedora` repository name is treated as historical;
the clone above is the currently redirected/current canonical location, to be
verified against GitHub metadata during the source audit.

Exact source versions matter. In the captured X710 source commit the README
identifies Fedora 44 and Linux stable 7.2; its `kernel/kernel.spec` pins
7.2.0 and the issue register documents Wi-Fi loss on 7.2.1–7.2.6, so X810
bring-up should begin from 7.2.0 unless code/history shows that regression has
been fixed. Revalidate before implementation.

Additional primary reference used for the first WLAN identification:

- Linux upstream `drivers/net/wireless/ath/ath11k/pci.c`, where
  `WCN6855_DEVICE_ID` is `0x1103` and the PCI ID table matches Qualcomm vendor
  `0x17cb`; URL: <https://github.com/torvalds/linux/blob/master/drivers/net/wireless/ath/ath11k/pci.c>.

Exact-firmware candidate reference (secondary index; download and package
hashes not yet verified): [XSP SM-X810 `X810XXS5CYG1` / `X810OXM5CYG1`](https://samfrew.com/download/Galaxy__Tab__S9__Plus__/fbup/XSP/X810XXS5CYG1/X810OXM5CYG1).
