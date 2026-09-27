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
    mic = section(hifi_text, 'SectionDevice."Mic" {', "\n}\n")
    verb = section(hifi_text, "SectionVerb {", 'SectionDevice."Speaker"')

    # The board routes two on-device digital microphones through the VA macro
    # to the MultiMedia3 capture PCM. Keep its enable/disable lifetime paired.
    for route in (
        'cset "name=\'VA DMIC MUX0\' DMIC0"',
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
            raise AssertionError(f"missing stereo DMIC source route item: {route}")

    for route in (
        'cset "name=\'MultiMedia3 Mixer VA_CODEC_DMA_TX_0\' 1"',
        'cset "name=\'MultiMedia3 Mixer VA_CODEC_DMA_TX_0\' 0"',
    ):
        if route not in verb:
            raise AssertionError(f"missing MultiMedia3 capture lifetime control: {route}")

    if 'File "/Qualcomm/sm8550/GTS9/HiFi.conf"' not in card_text:
        raise AssertionError("X810 sound-card profile no longer selects the HiFi route")

    print("PASS: X810 UCM declares the stereo DMIC -> VA -> MultiMedia3 capture path")
    print("NOTE: static contract only; actual microphone capture remains unverified")


if __name__ == "__main__":
    main()
