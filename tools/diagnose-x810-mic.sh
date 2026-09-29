#!/usr/bin/env bash
# Read-only X810 built-in microphone diagnostics.
# This reports route availability; it does not alter mixer controls, start a
# capture stream, or save audio. Run with an ALSA card index (default: 0).
set -uo pipefail

CARD="${1:-0}"
if [[ $# -gt 1 || ! "$CARD" =~ ^[0-9]+$ ]]; then
    echo "Usage: $0 [ALSA-card-index]" >&2
    exit 2
fi

section() { printf '\n== %s ==\n' "$1"; }
run_readonly() {
    local title="$1"
    shift
    section "$title"
    if command -v "$1" >/dev/null 2>&1; then
        "$@" 2>&1 || echo "(command exited $?)"
    else
        echo "not installed: $1"
    fi
}

section "Scope"
echo "Read-only checks for ALSA card $CARD. No recording or playback is started."
echo "For an actual capture validation, use a deliberate, separate recording command."

section "Detected ALSA cards"
if [[ -r /proc/asound/cards ]]; then
    cat /proc/asound/cards
else
    echo "/proc/asound/cards is unavailable"
fi

run_readonly "Capture devices" arecord -l
run_readonly "Capture PCM names" arecord -L
run_readonly "UCM card profiles" alsaucm listcards

section "X810 TX/VA-macro mixer controls (read only)"
if command -v amixer >/dev/null 2>&1; then
    controls="$(amixer -c "$CARD" controls 2>&1)" || {
        printf '%s\n' "$controls"
        echo "Could not query ALSA card $CARD. Check its index above."
        controls=""
    }
    for name in \
        "MultiMedia3 Mixer TX_CODEC_DMA_TX_3" \
        "TX DEC1 MUX" "TX DMIC MUX1" "TX_AIF1_CAP Mixer DEC1" "TX_DEC1 Volume" \
        "TX DEC2 MUX" "TX DMIC MUX2" "TX_AIF1_CAP Mixer DEC2" "TX_DEC2 Volume" \
        "MultiMedia3 Mixer VA_CODEC_DMA_TX_0" \
        "VA DMIC MUX0" "VA DMIC MUX1" "VA DEC0 MUX" "VA DEC1 MUX" \
        "VA_AIF1_CAP Mixer DEC0" "VA_AIF1_CAP Mixer DEC1" \
        "VA_DEC0 Volume" "VA_DEC1 Volume"; do
        if grep -Fq "name='$name'" <<<"$controls"; then
            printf '[present] %s\n' "$name"
            amixer -c "$CARD" cget "name='$name'" 2>&1 || true
        else
            printf '[missing] %s\n' "$name"
        fi
    done
else
    echo "not installed: amixer"
fi

section "MultiMedia3 capture PCM state"
PCM_STATUS="/proc/asound/card${CARD}/pcm2c/sub0/status"
if [[ -r "$PCM_STATUS" ]]; then
    cat "$PCM_STATUS"
    echo "'closed' is normal while no application is recording."
else
    echo "No status file at $PCM_STATUS (capture PCM may not have registered)."
fi

run_readonly "PipeWire-visible audio nodes" wpctl status -n

section "Relevant kernel messages from this boot (last 80 matches)"
if command -v journalctl >/dev/null 2>&1; then
    journalctl -k -b --no-pager 2>&1 \
        | grep -Ei 'lpass|dmic|audio|sound|q6apm|q6afe|q6prm|snd|asoc' \
        | tail -n 80 || true
else
    echo "not installed: journalctl"
fi

echo
echo "Diagnostic complete. No microphone data was captured or written."
