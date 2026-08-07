#!/usr/bin/env bash
# Define the ALSA capture device the pipeline records from. Run this ON THE PI,
# once per machine (and again if you change microphones).
#
#   bash scripts/setup_alsa.sh          # auto-detect the USB capture card
#   bash scripts/setup_alsa.sh 3        # force card 3
#
# Why this exists: the cheap USB capsule uses a PCM2902 codec, and PortAudio
# cannot open it directly at 16 kHz -- it fails with paInvalidSampleRate even
# though `arecord` will negotiate that rate happily. Wrapping the card in ALSA's
# `plug` plugin makes the conversion transparent, so the Python layer always
# gets the 16 kHz mono stream that whisper.cpp and the VAD expect.
#
# It also decouples us from the ALSA card number, which changes across reboots
# and between USB ports. Config refers to the stable name "voicekbmic".
set -euo pipefail

CARD="${1:-}"

if ! command -v arecord >/dev/null 2>&1; then
  echo "arecord not found. Install with: sudo apt install -y alsa-utils" >&2
  exit 1
fi

if [[ -z "$CARD" ]]; then
  CARD=$(arecord -l 2>/dev/null | sed -n 's/^card \([0-9]*\):.*USB.*/\1/p' | head -1)
  if [[ -z "$CARD" ]]; then
    # Fall back to the first capture card of any kind.
    CARD=$(arecord -l 2>/dev/null | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1)
  fi
  if [[ -z "$CARD" ]]; then
    echo "No ALSA capture card found. Is the mic plugged in? Try: arecord -l" >&2
    exit 1
  fi
  echo "Auto-detected capture card: $CARD"
fi

ASOUNDRC="$HOME/.asoundrc"
if [[ -f "$ASOUNDRC" ]] && ! grep -q "voicekbmic" "$ASOUNDRC"; then
  cp "$ASOUNDRC" "$ASOUNDRC.bak"
  echo "Existing ~/.asoundrc backed up to $ASOUNDRC.bak"
fi

cat > "$ASOUNDRC" <<EOF
# Written by scripts/setup_alsa.sh -- do not hand-edit; re-run the script.
#
# 'plug' converts sample rate and format on the fly, so clients may ask for
# 16 kHz mono even though this codec natively prefers 44.1/48 kHz.
pcm.voicekbmic {
    type plug
    slave.pcm "hw:${CARD},0"
    hint {
        show on
        description "voicekb mic (plug, rate-converting)"
    }
}
EOF

echo "Wrote $ASOUNDRC pointing voicekbmic -> hw:${CARD},0"
echo
echo "Verify PortAudio can see it:"
echo "  ./.venv/bin/python scripts/check_mic.py --list"
