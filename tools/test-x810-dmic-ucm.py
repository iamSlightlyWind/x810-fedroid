#!/usr/bin/env python3
"""Guard the intended X810 stereo DMIC UCM route.

This is a static source/packaging contract only. It does not parse ALSA UCM,
probe the tablet, or prove that either microphone records usable audio.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "rootfs/overlay/usr/share/alsa/ucm2"
CARD_LINK = OVERLAY / "conf.d/sm8550/Samsung-Galaxy-Tab-S9.conf"
CARD = OVERLAY / "Qualcomm/sm8550/GTS9/Samsung-Galaxy-Tab-S9.conf"
HIFI = OVERLAY / "Qualcomm/sm8550/GTS9/HiFi.conf"
DTS = ROOT / "kernel/files/sm8550-samsung-gts9wifi.dts"


def section(text: str, start: str, end: str) -> str:
    """Return a named source block, failing closed if its boundaries drift."""
    begin = text.find(start)
    if begin < 0:
        raise AssertionError(f"missing UCM section: {start}")
    finish = text.find(end, begin + len(start))
    if finish < 0:
        raise AssertionError(f"missing UCM section boundary after {start}: {end}")
    return text[begin:finish]


def main() -> None:
    if not CARD_LINK.is_symlink():
        raise AssertionError(f"missing ALSA UCM card-selection symlink: {CARD_LINK}")
    if CARD_LINK.resolve() != CARD.resolve():
        raise AssertionError("ALSA UCM card-selection symlink does not target the X810 profile")

    card_text = CARD.read_text(encoding="utf-8")
    hifi_text = HIFI.read_text(encoding="utf-8")
    dts_text = DTS.read_text(encoding="utf-8")
    mic = section(hifi_text, 'SectionDevice."Mic" {', "\n}\n")
    verb = section(hifi_text, "SectionVerb {", 'SectionDevice."Speaker"')
    sound = section(dts_text, "\tsound {", "\n\t};\n};")
    tx_macro = section(dts_text, "&lpass_txmacro {", "\n};")

    # CYG1's X810 gts9pwifi mixer_paths.xml routes main/sub mic through TX
    # DMIC1/3. Keep UCM, the DAI link, and the LPI DMIC pins on that same path.
    for route in (
        'cset "name=\'TX DEC0 MUX\' MSM_DMIC"',
        'cset "name=\'TX DMIC MUX0\' DMIC1"',
        'cset "name=\'TX DEC1 MUX\' MSM_DMIC"',
        'cset "name=\'TX DMIC MUX1\' DMIC3"',
        'cset "name=\'TX_AIF1_CAP Mixer DEC0\' 1"',
        'cset "name=\'TX_AIF1_CAP Mixer DEC1\' 1"',
        'cset "name=\'TX_DEC0 Volume\' 80%"',
        'cset "name=\'TX_DEC1 Volume\' 80%"',
        'CaptureChannels 2',
        'CapturePriority 100',
        'CapturePCM "hw:${CardId},2"',
    ):
        if route not in mic:
            raise AssertionError(f"missing X810 TX DMIC source route item: {route}")

    for route in (
        'cset "name=\'TX_AIF1_CAP Mixer DEC0\' 0"',
        'cset "name=\'TX_AIF1_CAP Mixer DEC1\' 0"',
        'cset "name=\'TX DMIC MUX0\' ZERO"',
        'cset "name=\'TX DMIC MUX1\' ZERO"',
    ):
        if route not in mic:
            raise AssertionError(f"missing paired TX capture disable route: {route}")
    if "VA DMIC" in mic or "VA_AIF1_CAP" in mic:
        raise AssertionError("X810 mic profile mixes the Ultra/reference VA path with TX")

    for route in (
        'cset "name=\'MultiMedia3 Mixer TX_CODEC_DMA_TX_3\' 1"',
        'cset "name=\'MultiMedia3 Mixer TX_CODEC_DMA_TX_3\' 0"',
    ):
        if route not in verb:
            raise AssertionError(f"missing MultiMedia3 TX capture lifetime control: {route}")

    if 'sound-dai = <&q6apmbedai TX_CODEC_DMA_TX_3>;' not in sound:
        raise AssertionError("X810 sound card does not expose the TX capture DMA lane")
    if 'sound-dai = <&lpass_txmacro 0>;' not in sound:
        raise AssertionError("X810 TX capture DAI is not connected to LPASS TX macro")
    if "VA_CODEC_DMA_TX_0" in sound or "lpass_vamacro" in sound:
        raise AssertionError("X810 capture DAI still includes the VA-macro backend")
    for pin_state in (
        "dmic01_default", "dmic23_default", "dmic45_default", "dmic67_default",
    ):
        if f"&{pin_state}" not in tx_macro:
            raise AssertionError(f"LPASS TX macro is missing the X810 LPI pin state {pin_state}")
    va_macro = section(dts_text, "&lpass_vamacro {", "\n};")
    if "pinctrl-0 = <&dmic01_default>" in va_macro:
        raise AssertionError("X810 LPI DMIC pinctrl is still assigned to the VA macro")

    if 'File "/Qualcomm/sm8550/GTS9/HiFi.conf"' not in card_text:
        raise AssertionError("X810 sound-card profile no longer selects the HiFi route")

    print("PASS: X810 stock TX DMIC1/3 -> TX macro -> MultiMedia3 route is consistent")
    print("NOTE: static contract only; actual microphone capture remains unverified")


if __name__ == "__main__":
    main()
