# X810 CDSP/NPU firmware staging (experimental, source-side only)

The port has an offline validator for the SM-X810's two CDSP firmware load
ranges. This staging helper pins the exact stock **X810 XSP X810XXS5CYG1**
source image and all 43 extracted CDSP/DSP files. It deliberately writes only
under ignored `local-assets/`; the default rootfs and public release workflows
do not consume that directory, and staging does not enable `remoteproc_cdsp`.

The proprietary Samsung payload is not stored in Git or redistributed. Obtain
the official Samsung BL package for the device, then extract/decompress the two
members locally (the `.tar.md5` archive can be used directly by tar):

```sh
BL=/path/to/BL_X810XXS5CYG1_...tar.md5
tar -xOf "$BL" NON-HLOS.bin.lz4 | lz4 -dc > /tmp/x810-NON-HLOS.bin
tar -xOf "$BL" dspso.bin.lz4 | lz4 -dc > /tmp/x810-dspso.bin
python3 tools/stage-x810-cdsp-firmware.py \
  --non-hlos /tmp/x810-NON-HLOS.bin \
  --dspso /tmp/x810-dspso.bin \
  --output local-assets/npu-firmware-x810-cyg1
```

Alternatively, an owner may stage the matching files copied from an already
rooted Android installation. Prepare an extracted input tree containing only
the locked paths under `cdsp/` and `dsp/cdsp/` (for this image those correspond
to `/vendor/firmware_mnt/image/` and `/vendor/dsp/cdsp/` respectively), then
run:

```sh
python3 tools/stage-x810-cdsp-firmware.py \
  --verified-tree /path/to/extract \
  --output local-assets/npu-firmware-x810-cyg1
```

This alternative checks the exact 43-file allowlist and SHA256 values and runs
the same MDT carveout validator before staging. It does not install firmware,
start remoteproc, or make the files part of a rootfs or release. The owner has
verified that the current tablet's Android vendor copies match all 43 locked
hashes; no firmware blobs are kept in this checkout.

The helper checks both decompressed source-image SHA256 values, exact firmware
file set and SHA256 lock, all CDSP MDT segment bounds/sizes against the retained
X810 DT carveouts, and only then atomically stages files under the ignored local
cache. It refuses unknown firmware revisions rather than substituting X910 or
X710 images. Future stock firmware versions require a separately reviewed
manifest, not relaxed hash checking.

The X810 device tree records the two exact PAS requests as
`qcom/sm8550/cdsp.mdt` and `qcom/sm8550/cdsp_dtb.mdt`; that matches the staging
helper's `/usr/lib/firmware/qcom/sm8550/` layout. Linux's SM8550 PAS driver
uses the first `firmware-name` entry for the main CDSP image and the second
for the separately authenticated CDSP DT image. The CDSP node remains
explicitly `status = "disabled"`, and the staged local cache is not copied
into the default rootfs or public release. The DT path contract is therefore
prepared, not an NPU enablement claim.

## Offline source audit

The port uses upstream Linux 7.2's SM8550 definitions. In that source,
`sm8550.dtsi` defines the CDSP firmware carveout at `0x9c900000` (32 MiB) and
the separately authenticated CDSP-DT carveout at `0x9e900000` (512 KiB), and
the CDSP node references those regions in that order. The matching PAS
descriptor uses PAS ID 18, DT PAS ID `0x25`, and defaults to auto-boot. Its
probe reads firmware-name index 0 for the CDSP MDT and index 1 for the CDSP-DT
MDT. The port's property-gated `qcom,no-auto-boot` patch changes only the
auto-boot flag; it neither changes the still-disabled X810 node nor starts
remoteproc. Linux's MDT loader treats the hash segment as authentication
metadata and fetches loadable split payloads by the `.bNN` names. The offline
validator checks ELF32 little-endian/version fields, per-image machine values,
each loadable segment's file/memory size relation, exact carveout bounds, and
required payload sizes. The locked X810 CYG1 primary `cdsp.mdt` has ELF
`e_machine=164` (Hexagon), while the separately authenticated
`cdsp_dtb.mdt` has `e_machine=1`; the parser pins those per-image values rather
than requiring both to identify as Hexagon. Linux's MDT loader uses the ELF
program-header fields and PAS authentication here; it does not dispatch on
`e_machine`. These checks validate layout only, not secure authentication or
firmware compatibility.

Primary source references: [Linux v7.2 SM8550 device tree](https://github.com/torvalds/linux/blob/v7.2/arch/arm64/boot/dts/qcom/sm8550.dtsi), [Qualcomm PAS remoteproc driver](https://github.com/torvalds/linux/blob/v7.2/drivers/remoteproc/qcom_q6v5_pas.c), and [Qualcomm MDT loader](https://github.com/torvalds/linux/blob/v7.2/drivers/soc/qcom/mdt_loader.c). The local Tab S9 Ultra implementation is useful as a porting reference, but it uses its own `samsung,gts9uwifi` board check, separate experimental kernel changes and runtime validation; those do not establish X810 CYG1 compatibility.

This does **not** make the NPU usable yet. Successful PAS boot, FastRPC/GLINK
intent negotiation, actual HTP execution, numerical result checking, teardown,
idle wake, and thermal validation remain separate gates. The stock CDSP node is
left disabled and no service/module auto-start is installed by this helper.
Do not copy this cache into the default rootfs or release until device-specific
runtime tests and Qualcomm/Samsung redistribution terms are resolved.

## Optional activation and runtime-package boundary

The current SM8550 PAS descriptor in `drivers/remoteproc/qcom_q6v5_pas.c`
sets `auto_boot = true`; `rproc_add()` immediately invokes remoteproc's
auto-boot path when its DT node is enabled. Simply changing the CDSP node to
`okay` would therefore start secure firmware during kernel probe, before an
operator could inspect firmware readiness or userspace setup. This port carries
the opt-in `qcom,no-auto-boot` support from the Tab S9 Ultra reference and sets
that property on the X810 node, while keeping `status = "disabled"` in the
shipped DTB. If a future test kernel deliberately enables the node, the
property lets userspace request an explicit `echo start` instead of silently
booting CDSP. It does not make disabled remoteproc/FastRPC devices appear in
the default image.

The Fedora image already builds the upstream PAS/FastRPC kernel support and
packages `hexagonrpcd-samsung` for ADSP root/sensor protection domains. That
daemon and its units are not an HTP/QNN inference runtime. There is no QNN HTP
backend, QNN model runtime, session supervisor, or CDSP inference service in the
X810 rootfs or public support RPM. The Tab S9 Ultra NPU stack instead brings an
isolated ONNX Runtime/QNN HTP runtime plus board-specific FastRPC/session and
kernel adaptations. Its source gate is `samsung,gts9uwifi`, not this X810's
`samsung,gts9pwifi`.

The reference's stock CDSP image hashes are also different from X810 CYG1:
its `cdsp.mdt` is
`312ef45e0e71d784a4f1f3c02f4706556f1ff636a5a8af2ec36e51f3b99d6e1a`, whereas
the X810 CYG1 lock is
`ca6fef2d36e4078d30dc09d9b93901a6717faba4cb81b4513d96a8bebb44a501`; the
`cdsp_dtb.mdt` hashes differ as well. This confirms that copying the reference
firmware package or its successful runtime result is not evidence of X810
compatibility. The owner independently verified all 43 file hashes against
the tablet's Android `/vendor` firmware and confirmed that all 14 CDSP load
segments fit the retained CDSP and CDSP-DT carveouts. The corresponding blobs
were only copied to temporary audit storage; they are not present in this
checkout's ignored cache. This evidence does not validate PAS authentication,
remoteproc startup, FastRPC/HTP operation, or teardown. They remain
uncommitted and are not staged into images or releases.

**Conclusion:** a local firmware-only RPM could be packaged while the node
remains disabled, but it would be inert and would not be an NPU fix. A usable
optional X810 firmware/runtime package is not supported by current source or
runtime evidence: QNN/FastRPC compatibility with the X810 CYG1 firmware has
not been demonstrated, and no X810 PAS start, HTP inference, teardown, suspend,
or thermal test has passed. Do not publish or auto-install an NPU package yet.
The next meaningful gate is a separately reviewed test kernel plus the
owner's validated firmware on the physical X810, with manually controlled
remoteproc start/stop and logs retained; only after that should a local runtime
package be designed.
