#!/usr/bin/env bash
# Raise the ALSA *hardware* capture gain on the USB mic.
#
# This is the first knob to turn when the mic is too quiet. It amplifies before
# the signal reaches the noise floor of the rest of the chain, so it costs you
# far less SNR than software gain does.
#
#   scripts/set_gain.sh            # auto-detect the USB capture card, set 80%
#   scripts/set_gain.sh 1 90       # card 1, 90%
set -euo pipefail

CARD="${1:-}"
PERCENT="${2:-80}"

if ! command -v amixer >/dev/null 2>&1; then
  echo "amixer not found. Install with: sudo apt install -y alsa-utils" >&2
  exit 1
fi

if [[ -z "$CARD" ]]; then
  # First card that offers capture. arecord -l lines look like "card 1: Device ..."
  CARD=$(arecord -l 2>/dev/null | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1)
  if [[ -z "$CARD" ]]; then
    echo "No ALSA capture card found. Is the mic plugged in? Try: arecord -l" >&2
    exit 1
  fi
  echo "Auto-detected capture card: $CARD"
fi

echo "=== Controls on card $CARD ==="
amixer -c "$CARD" scontrols || true

found=0
while IFS= read -r name; do
  [[ -z "$name" ]] && continue
  # Only touch controls that actually expose a capture volume.
  if amixer -c "$CARD" sget "$name" 2>/dev/null | grep -q 'Capture'; then
    echo "--- setting '$name' to ${PERCENT}% and unmuting"
    amixer -c "$CARD" sset "$name" "${PERCENT}%" cap 2>/dev/null \
      || amixer -c "$CARD" sset "$name" "${PERCENT}%" 2>/dev/null \
      || echo "    (could not set '$name'; skipping)"
    found=1
  fi
done < <(amixer -c "$CARD" scontrols | sed -n "s/^Simple mixer control '\(.*\)',[0-9]*$/\1/p")

if [[ "$found" -eq 0 ]]; then
  echo
  echo "No capture control found on card $CARD. Some very cheap USB mics expose" >&2
  echo "no mixer at all -- their gain is fixed in hardware. In that case raise" >&2
  echo "audio.software_gain in config/default.yaml instead." >&2
  exit 2
fi

# Persist across reboot. Needs root; ignore failure when run unprivileged.
if command -v alsactl >/dev/null 2>&1; then
  sudo alsactl store 2>/dev/null && echo "Saved mixer state (persists across reboot)." \
    || echo "Note: run 'sudo alsactl store' to persist across reboot."
fi

echo
echo "Done. Re-run: python3 scripts/check_mic.py"
