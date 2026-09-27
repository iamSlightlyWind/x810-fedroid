# SM-X810 hardware notebook (initial, evidence-led)

This is a research notebook, not a complete DTS. Entries labeled measured
come from the connected SM-X810; sibling-device details are hypotheses only.
Raw captures are in git-ignored `probes/android-baseline/`.

## Board identity

- **Measured:** `SM-X810`, `gts9pwifi`, `gts9pwifixx`, Qualcomm SM8550/Kalama,
  arm64. Firmware build `X810XXS5CYG1`.
- **Measured running FDT:** `compatible` list `qcom,kalama`,
  `qcom,kalama-mtp`, `qcom,mtp`; model string
  `Samsung GTS9PWIFI PROJECT (board-id,04)`; `qcom,board-id` cells
  `<0x00010008 0x00000004>`.
- **Open:** these values describe the current stock runtime FDT. Establish
  which exact selector tuples Samsung ABL consumes and what X810-specific
  mainline root compatibles it accepts before writing a test image.

## WLAN

- **Measured:** PCI endpoint `0000:01:00.0`, vendor/device `17cb:1103`,
  subsystem `17cb:0108`; stock driver `cnss_pci`.
- **Strong identification:** upstream ath11k's PCI ID table names Qualcomm
  `17cb:1103` WCN6855. This places X810 on the WCN6855/ath11k path, unlike the
  X910 WCN7850/ath12k path. Source: upstream Linux ath11k PCI table; exact
  source is linked in `SOURCE-PROVENANCE.md`.
- **Not established:** compatible mainline firmware family, BDF/calibration,
  AOP PDC settings, MAC source, cold-boot behavior, radio/regulatory behavior.
  Do not stage sibling firmware or modify `persist` until these are sourced.
- **Exact CYG1 downstream evidence:** X810 overlay calls the platform device
  `qcom,cnss-qca6490`, references a 32 MiB dynamic WLAN pool, and configures
  WLAN enable on GPIO 80. X710's current port describes the same QCA6490 /
  WCN6855-class PCIe path and uses ath11k, making its existing workaround set
  an unusually close starting point. Preserve X810-specific sequencing,
  firmware/calibration and GPIO assumptions until validated on X810.

## Display

- **Measured:** stock cmdline `msm_drm.dsi_display0=GTS9P_ANA38407_AMSA24VU05:`
  and `msm_drm.lcd_id=800005`; FDT board string says GTS9PWIFI board-id 04.
- **Exact-source match found:** the CYG1-matched Samsung source package includes
  `GTS9P_ANA38407_AMSA24VU05_panel.c` and related headers/config/data under
  `vendor/qcom/opensource/display-drivers/msm/samsung/`, matching the live
  panel string. This establishes the stock panel implementation identity, not
  that its vendor driver can be used as-is in mainline.
- **Promising prior art:** X710 Fedora contains a mainline ANA38407 panel
  driver, and its source describes the same DDIC family. It explicitly keeps
  board-specific initialization/timings separate; therefore the driver is a
  code reference, not an X810-ready panel configuration.
- **Exact X810 panel facts:** live LCD ID `0x800005` maps to panel revision E
  in the CYG1 downstream panel driver. The selected stock DT timing is
  2800×1752, with 30/60/120 Hz modes and DSC 1.1 at 8 bpp, two 1400×73-pixel
  slices. The Samsung data file's D-to-Z branches include revision E for the
  shared TSP-sync sequence, so the ANA38407 initialization is a promising
  base.
- **Concrete X710 mismatch:** its mainline driver hardcodes the expected DDIC
  ID to revision D (`80:00:04`), and uses 2560×1600, two 1280×100 DSC slices,
  and X710 board-specific timings. X810's live FDT maps panel reset to TLMM
  GPIO 125 and TE to TLMM GPIO 86, matching the X710 mainline panel node. Its
  `panel_ldo_en` is a 1.8 V fixed regulator on TLMM GPIO 187 with a 100 mA
  enable load, 11 ms post-on and 15 ms pre-off waits; X710's mainline DTS
  models that same GPIO/rail. This is useful board-level corroboration. The
  CYG1's DSI regulator references resolve to PMIC L12B (1.8 V `vddio`), L11B
  (1.2 V `vdd`), and L13B (3.0 V `vci`); its panel supply tables independently
  specify these same rails and an `avdd` supply at 5.5 V. The runtime FDT also
  describes `display_panel_avdd` as a 5.5 V proxy regulator, but does not
  establish the same GPIO-backed implementation used by X710. Thus the four
  mainline regulator names have stock X810 rail counterparts, but the exact
  `avdd` backend and sequencing still need confirmation before reuse. X810's
  rev-E ID is not rejected by the driver but triggers its
  dark-until-DSI-reinitialization warning. The X710 driver must not be used
  unchanged; port X810's exact timing/DSC/rail/GPIO parameters and accept/test
  the rev-E ID first.
- **Open:** compare the X810 PHY timings, porch values, DSC PPS and full
  regulator ordering against mainline before selecting first-light mode; in
  particular, identify the physical `avdd` switch and active supply-table
  selection. Physical cold-boot/resume validation remains necessary.

### X810-specific display power evidence (CYG1)

- The X810 CYG1 source adds an important distinction from the X710 port:
  `GTS9P_ANA38407_AMSA24VU05_panel.c::ss_boost_control()` programs its
  MAX77816 display-boost device at I2C address `0x18` (stock FDT node
  `max77816,display_boost`). It writes register `0x03 = 0x70` (boost enable)
  and `0x02 = 0x8e` (the Samsung comment says 3.1 A; MAX77816's register map
  identifies this as the inductor peak-current limit; see the
  [MAX77816 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/max77816.pdf).
  The FDT separately
  shows PM8550 GPIO11
  in the `display_panel_avdd_default` pinctrl state and a
  `display_panel_avdd` proxy regulator, but does **not** wire that regulator
  node to GPIO11. Do not copy X710's GPIO11-backed fixed-regulator assumption
  onto X810; native Linux must either model the MAX77816 path correctly or
  establish that ABL leaves the boost configured for the whole Linux session.
- X810 panel command source confirms `POWER_ON_PRE_SETTING`: X810-specific VBP
  and display-on-delay settings, then sleep-out (`0x11`),
  MX-IP/TCON/touch-sync setup, DSC PPS, DIA/brightness/SP setup,
  50 ms delay and VRR; `POWER_ON_POST_SETTING` unlocks level 0, sends
  display-on (`0x29`) and re-locks. The X710 mainline driver also injects
  `SLEW_BOOSTING_OFF/ON` register sequences not present in the X810 CYG1
  sequence; don't carry those over blindly. `POWER_OFF_SETTING` sends display-off
  and sleep-in, then waits 100 ms. The selected X810 panel FDT reset sequence
  is `<0 10 1 1>` (drive low 10 ms, then high 1 ms); consume this exact
  polarity/timing rather than copying X710's pulse sequence. `panel_ldo_en` is
  GPIO187, 1.8 V, with 11 ms post-on and 15 ms pre-off delays. The panel's
  selected 60 Hz DSC mode is 2800×1752, 2×(1400×73) slices at 8 bpp; PPS
  RC tables/offsets match the
  X710 driver, but its geometry/chunk size differ (1400 rather than 1280).
  X810 reports LCD ID `80:00:05` (revision E), not the X710 driver's expected
  `80:00:04` (revision D).
- Decoding the X810 stock DSC payload gives DSC 1.1, 8 bpc / 8 bpp, 9-bit line
  buffer, 1752×2800 picture, 1400×73 slice, 1400-byte chunk; transmit delay
  512, decoder delay 957, scale interval 2634/19, initial/final offsets
  6144/4304, and flatness QP 3–12. The remaining rate-control settings match
  the X710 driver's DSC 8-bpp tables. That makes the X710 `drm_dsc_config` a
  useful template after changing geometry, but its panel init/timings are not
  a safe drop-in.

## Touch and S Pen

- **Measured logs:** `fts_touch` at bus address `0x49`; `wacom_w90xx` at
  `0x56`. Current kernel bus identifiers 59 and 58 are downstream numbering,
  not DTS aliases.
- **Open:** map controllers to stock DT, interrupts, reset, supplies, firmware,
  Wacom dock/charger/BLE behavior and compare against X710 FTS and X910 Wacom
  implementations only after confirming exact compatible/register protocol.
- **Exact-source update:** X810 r04 source confirms STM FTS1BA90A at `0x49`
  with firmware `tsp_stm/fts1ba90a_gts9p.bin`, and Wacom W90xx at `0x56` with
  `wez01_gts9p.bin`. This is a strong match to the X710 port's existing FTS
  and Wacom driver families, though X810 regulator/GPIO/coordinate details
  require separate porting and physical tests. The same overlay also declares
  Goodix GT9916 at `0x5d` as `qcom,i2c-touch-active` with trusted-touch/VM
  properties; its runtime role is not yet proven, so do not conflate it with
  the FTS touchscreen that appears in live logs.

## Memory, UFS, other peripherals

- Live memory map and complete live FDT were captured; no safe mainline memory
  map has yet been produced. This is a hard gate before booting Linux.
- The three additional X810 runtime reservations `kaslr_region`,
  `uh_heap_region`, and `uh_guest_region` have matching base addresses in both
  X710 and X910 mainline-port DTS files. The X810 live FDT explicitly marks
  only KASLR `no-map`; all three are reserved in X810 `/proc/iomem`. The UH
  naming suggests a hypervisor tie but does not prove the owner. The X810
  `uh_guest` range is 54 MiB, versus 48 MiB in X710 and 58 MiB in X910; retain
  only X810's measured size. See `MEMORY-MAP.md` for source commits and the
  remaining `no-map` semantics question. Sibling fatal-NoC history reinforces
  the reservation gate but is not an X810 boot result.
- `sda` is observed as a 249,102,336-sector block device with partitions
  through `sda34`; `userdata` is mapped to `sda34`. Do not infer GPT boundaries
  or use partition numbers for any write based on this alone.
- No other subsystem is yet sufficiently resolved for DTS implementation.

## Native desktop input bring-up (2026-09-25)

- The native MSM DPU/DSI path now reports the X810 panel connected/enabled at
  2800×1752; GDM remains up and GNOME Shell has committed a full-size DRM
  framebuffer. This is the first graphical login screen, not just fbcon text.
- The FTS1BA90A binds and emits MT events. GNOME initially selected a
  180° transform; set the built-in DSI output to normal transform 0 through
  Mutter's DisplayConfig API, and confirmed that setting survived a Linux
  reboot. A libinput capture sees touch events, but the captured coordinates
  (`2621,-129` on axes bounded to `2559 × 1599`) need a physical position check
  before claiming accurate touchscreen mapping.
- The EF-DX815 is **not Bluetooth input**: X810 CYG1 DTBO declares its STM32
  POGO controller at I²C `0x2a` on QUPv3 SE15, plus VDDO/BOOT/RESET/connection/
  data-ready GPIOs and a separate MAX77816 accessory-boost bus. The running
  Linux DT lacked SE15 and the accessory nodes, so no keyboard I²C client or
  keyboard input existed. The stock X810 model table identifies `0xfb` as
  EF-DX815 and supports its touchpad.
- Flashed the candidate only to `boot` and `vendor_boot`; flashed the
  already-present 4 KiB invalid-DTBO fallback (partition `dtbo`) so Samsung's
  downstream board overlay would not overwrite mainline nodes. The original
  CYG1 DTBO is preserved at `probes/android-baseline/live-boot-images/dtbo.img`.
  Download-mode/bootloader partitions and GPT were untouched. The fallback
  handed the candidate board tree through: SE15 and MAX77816 now probe, and the
  STM32 bootloader responds with product `0x460`, flash version `00 34 00 34`.
  Linux reports `connected=0`, `attached=0`, no model, and no keyboard input
  device; this is either an absent/undetected pogo attachment or a connection
  GPIO/polarity issue, not a missing driver/I²C bus. The keyboard firmware
  update path was not invoked. Read-back images are saved under
  `work/x810-smoke/backups/after-pogo-boot/`.
- GDM is active. The panel rotation is now normal in Mutter; a 20-second
  timeout drop-in was added for the nonessential sensor-proxy boot wait, which
  otherwise held GDM for 210 seconds while SSC was unavailable.
- Input research references: X810 CYG1 Samsung kernel source in the ignored
  local directory `work/firmware-cyg1/samsung-source/.../gts9pwifi_eur_open_w00_r04.dts`,
  [upstream X910 pogo implementation](https://github.com/agcarbajo/ubuntu-galaxy-tab-s9-ultra/tree/main/kernel),
  and [S9-family Fedora touchscreen port](https://github.com/nacht20-de/gts9wifi-fedora-linux).

## Camera stack audit (2026-09-25)

- The running kernel exposes `/dev/media0`, V4L2 nodes, the SM8550 ISP, and
  sensor/actuator subdevices. This proves enumeration, not that image capture
  works; no camera stream or image was opened during this audit.
- The video and media nodes are `root:video` mode `0660`; the desktop account
  was not in `video`, which prevents camera clients from opening them. The
  clean-install script now creates the chosen UID-1000 account in `video`, and
  the updater RPM repairs that membership on existing installs. The already
  running desktop session retains its old supplementary groups until a new
  login. This fixes the permission failure, not intermittent sensor/ISP
  discovery; actual camera capture still needs a live test.
- The installed rootfs already contains libcamera and PipeWire's libcamera
  SPA plugin. Its GNOME camera app was absent. Fedora 44 names that RPM
  `snapshot` (not `gnome-snapshot`); the rootfs builder now installs the
  current package name together with the camera stack. This local fix has not
  yet been rebuilt or installed on the tablet.

## Haptic support preparation (2026-09-25)

- The X810 CYG1 overlay explicitly describes its coin-DC vibrator on active-
  high TLMM GPIO18. The X910 port uses the upstream `gpio-vibrator` driver on
  the same line, and its README reports working haptics. I added the equivalent
  device-tree node to the X810 research DTS and enabled `CONFIG_INPUT_GPIO_VIBRA`.
- The X810 DTS target and vibrator driver compile locally. This is prepared
  kernel-source work only: it has not been flashed or exercised on the X810,
  so the haptics status remains unverified. No vibration test was sent.
