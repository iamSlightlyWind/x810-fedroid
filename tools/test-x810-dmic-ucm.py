#!/usr/bin/env python3
"""Guard the X810's reference-backed VA-macro DMIC route.

Static source/packaging checks only. This does not prove live microphone audio.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "rootfs/overlay/usr/share/alsa/ucm2"
CARD_LINK = OVERLAY / "conf.d/sm8550/Samsung-Galaxy-Tab-S9.conf"
CARD = OVERLAY / "Qualcomm/sm8550/GTS9/Samsung-Galaxy-Tab-S9.conf"
HIFI = OVERLAY / "Qualcomm/sm8550/GTS9/HiFi.conf"
DTS = ROOT / "kernel/files/sm8550-samsung-gts9wifi.dts"
DIAGNOSTIC = ROOT / "tools/diagnose-x810-mic.sh"


def section(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        raise AssertionError(f"missing source section: {start}")
    finish = text.find(end, begin + len(start))
    if finish < 0:
        raise AssertionError(f"missing section boundary after {start}: {end}")
    return text[begin:finish]


def main() -> None:
    if not CARD_LINK.is_symlink() or CARD_LINK.resolve() != CARD.resolve():
        raise AssertionError("ALSA UCM selection symlink does not target the X810 profile")

    card_text = CARD.read_text(encoding="utf-8")
    hifi_text = HIFI.read_text(encoding="utf-8")
    dts_text = DTS.read_text(encoding="utf-8")
    mic = section(hifi_text, 'SectionDevice."Mic" {', "\n}\n")
    verb = section(hifi_text, "SectionVerb {", 'SectionDevice."Speaker"')
    sound = section(dts_text, "\tsound {", "\n\t};\n};")
    va_macro = section(dts_text, "&lpass_vamacro {", "\n};")

    if "physical sub-main pair DMIC3/DMIC1" not in card_text:
        raise AssertionError("card profile does not distinguish the CYG1 physical mic mapping")
    if "experiments with the VA-macro" not in card_text:
        raise AssertionError("card profile overstates VA-macro support as the stock CYG1 route")

    # CYG1 HAL evidence identifies DMIC3 + DMIC1 on the TX macro for ordinary
    # sub-main stereo recording. The reference-backed VA-macro route below is
    # a Linux backend experiment, not the stock Android signal path.
    for route in (
        'cset "name=\'VA DMIC MUX0\' DMIC3"',
        'cset "name=\'VA DMIC MUX1\' DMIC1"',
        'cset "name=\'VA DEC0 MUX\' VA_DMIC"',
        'cset "name=\'VA DEC1 MUX\' VA_DMIC"',
        'cset "name=\'VA_AIF1_CAP Mixer DEC0\' 1"',
        'cset "name=\'VA_AIF1_CAP Mixer DEC1\' 1"',
        'cset "name=\'VA_DEC0 Volume\' 80%"',
        'cset "name=\'VA_DEC1 Volume\' 80%"',
        'CaptureChannels 2',
        'CapturePriority 100',
        'CapturePCM "hw:${CardId},2"',
    ):
        if route not in mic:
            raise AssertionError(f"missing X810 VA-macro capture route item: {route}")
    for route in (
        'cset "name=\'VA_AIF1_CAP Mixer DEC0\' 0"',
        'cset "name=\'VA_AIF1_CAP Mixer DEC1\' 0"',
        'cset "name=\'VA DMIC MUX0\' ZERO"',
        'cset "name=\'VA DMIC MUX1\' ZERO"',
    ):
        if route not in mic:
            raise AssertionError(f"missing paired VA-macro capture disable item: {route}")
    if "TX_AIF1_CAP" in mic or "TX DMIC" in mic:
        raise AssertionError("mic UCM still selects the TX-macro capture path")

    for route in (
        'cset "name=\'MultiMedia3 Mixer VA_CODEC_DMA_TX_0\' 1"',
        'cset "name=\'MultiMedia3 Mixer VA_CODEC_DMA_TX_0\' 0"',
    ):
        if route not in verb:
            raise AssertionError(f"missing MultiMedia3 VA capture lifetime control: {route}")

    if 'sound-dai = <&q6apmbedai VA_CODEC_DMA_TX_0>;' not in sound:
        raise AssertionError("X810 sound card does not expose the VA capture DMA lane")
    if 'sound-dai = <&lpass_vamacro 0>;' not in sound:
        raise AssertionError("X810 VA capture DAI is not connected to the VA macro")
    if "TX_CODEC_DMA_TX_3" in sound or "lpass_txmacro" in sound:
        raise AssertionError("X810 capture link still uses TX macro")
    for pin_state in (
        "dmic01_default", "dmic23_default", "dmic45_default", "dmic67_default",
    ):
        if f"&{pin_state}" not in va_macro:
            raise AssertionError(f"VA macro is missing X810 LPI pin state {pin_state}")
    if "qcom,dmic-sample-rate = <4800000>" not in va_macro:
        raise AssertionError("VA macro DMIC clock divider sample rate is missing")
    if "&lpass_txmacro {" in dts_text:
        raise AssertionError("X810 DMIC pinctrl is still assigned to the TX macro")

    if 'File "/Qualcomm/sm8550/GTS9/HiFi.conf"' not in card_text:
        raise AssertionError("X810 sound-card profile no longer selects its HiFi route")
    if not DIAGNOSTIC.stat().st_mode & 0o111:
        raise AssertionError("X810 microphone diagnostic is not executable")
    diagnostic = DIAGNOSTIC.read_text(encoding="utf-8")
    for evidence in (
        "arecord -l", "MultiMedia3 Mixer TX_CODEC_DMA_TX_3",
        "MultiMedia3 Mixer VA_CODEC_DMA_TX_0", "VA DMIC MUX1",
        "pcm2c/sub0/status", "wpctl status",
    ):
        if evidence not in diagnostic:
            raise AssertionError(f"mic diagnostic omits required read-only evidence: {evidence}")
    if "arecord -D" in diagnostic or "speaker-test" in diagnostic:
        raise AssertionError("mic diagnostic must not capture audio or play a test tone by default")

    print("PASS: X810 VA capture experiment preserves the CYG1 physical DMIC3/1 mapping")
    print("NOTE: static contract only; controlled live input and intelligibility remain unverified")


if __name__ == "__main__":
    main()
