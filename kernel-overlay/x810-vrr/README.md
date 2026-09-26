# X810 ANA38407 120 Hz source overlay

This is a source-only overlay for the X810 SM-X810 (`AMSA24VU05`, LCD ID
`80:00:05`) panel driver in Linux 7.2. It keeps the existing 2800x1752@60
mode first/preferred and adds the CYG1 stock 120-Hz timing plus its DDIC VRR
writes. The selected mode is recorded through an optional DRM-panel mode
notification and programmed in `prepare`, after the DSI command path is ready.
No boot image, DTB, partition, or tablet state is modified by this overlay.

## Provenance and checked values

The stock source is `SM-X810_15_Opensource.zip` from the downloaded X810 CYG1
firmware bundle (`Kernel.zip`), specifically the Samsung `GTS9P_ANA38407_AMSA24VU05`
`.dat` VRR_SETTING and matching CYG1 `dtbo.img` profile `wqxga120hs`.
The stock timing is HFP/HBP/HSW=64/48/64 and VFP/VBP/VSW=48/48/64,
2800x1752, 120 Hz: totals 2976x1912, pixel clock 682813.44 kHz (DRM rounded
to 682813 kHz). The 60-Hz stock/default mode remains 3460x2392, 496579.2 kHz.
VRR DCS writes preserve the vendor order and values: unlock F0/F1, register
0x60 (00 for 120HS, 10 for 60HS), indirect DD=00, revision-D-to-Z B9 tuning
(80 00 00 00 for 120HS; AA AA AA AA for 60HS), then relock.

The live Linux DRM DSI connector currently exposes only 2800x1752@60. The
running device tree supplies a 4-lane DSI link and OPPs 187.5/300/358 MHz.
The MSM compressed-mode clock calculation for the proposed 120-Hz timing is
about 191.0-MHz DSI byte clock (1.528-Gbit/s per lane), below the 300-MHz OPP;
the SM8550 4-nm DSI PHY config permits a 5-GHz PLL. This is a source-level
bandwidth check, not proof of physical signal integrity or panel scanout.

## Apply and build (does not install or flash)

```sh
kernel-overlay/x810-vrr/apply.sh /path/to/linux-v7.2
cd /path/to/linux-v7.2
make ARCH=arm64 LLVM=1 -j8 drivers/gpu/drm/bridge/panel.o \
    drivers/gpu/drm/panel/panel-samsung-ana38407.o
```

The live-tree validation used those two object targets and succeeded. To link
all enabled kernel objects as an additional check:

```sh
make ARCH=arm64 LLVM=1 -j8 vmlinux
```

This overlay has not been installed or tested on hardware. Do not make 120 Hz
the preferred/default mode until on-device mode-switch, cold-boot, suspend,
and resume tests pass. The existing 60-Hz mode remains preferred.
