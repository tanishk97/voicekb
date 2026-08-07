#!/usr/bin/env bash
# Benchmark whisper.cpp models against a real recording. Run this ON THE PI.
#
#   bash scripts/bench_whisper.sh /tmp/speech.wav
#
# Reports wall time and realtime factor for each model, plus SoC temperature
# before and after. Temperature matters: with no active cooler, sustained
# inference throttles, and a model that is fast on the first run can be
# noticeably slower on the tenth.
#
# Input must be 16 kHz mono WAV -- which is exactly what check_mic.py writes.
set -euo pipefail

WAV="${1:-/tmp/speech.wav}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$REPO_ROOT/vendor/whisper.cpp/build/bin/whisper-cli"
MODELS="$REPO_ROOT/models"
THREADS="${THREADS:-$(nproc)}"

if [[ ! -f "$WAV" ]]; then
  echo "No such file: $WAV" >&2
  echo "Record one with: ./.venv/bin/python scripts/check_mic.py --out $WAV" >&2
  exit 1
fi
if [[ ! -x "$BIN" ]]; then
  echo "whisper-cli not built. Run: bash scripts/setup_whisper.sh" >&2
  exit 1
fi

# Duration in seconds, from the WAV header (16 kHz, mono, 16-bit => 32000 B/s).
bytes=$(stat -c%s "$WAV")
dur=$(awk -v b="$bytes" 'BEGIN{printf "%.2f", (b-44)/32000}')

echo "input      : $WAV"
echo "duration   : ${dur}s"
echo "threads    : $THREADS"
echo "temp before: $(vcgencmd measure_temp)"
echo

for model in "$MODELS"/ggml-*.bin; do
  [[ -f "$model" ]] || continue
  name=$(basename "$model" .bin | sed 's/^ggml-//')
  for mode in greedy beam; do
    if [[ "$mode" == greedy ]]; then extra=(-bs 1); else extra=(-bs 5); fi
    start=$(date +%s.%N)
    out=$("$BIN" -m "$model" -f "$WAV" -t "$THREADS" -nt -l en "${extra[@]}" 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g')
    end=$(date +%s.%N)
    el=$(awk -v s="$start" -v e="$end" 'BEGIN{printf "%.2f", e-s}')
    rtf=$(awk -v e="$el" -v d="$dur" 'BEGIN{printf "%.2f", e/d}')
    printf '%-18s %-7s %6ss  %5sx realtime  %s\n' "$name" "$mode" "$el" "$rtf" "$(vcgencmd measure_temp)"
    printf '  -> %s\n' "$(echo "$out" | cut -c1-120)"
  done
done

echo
echo "temp after : $(vcgencmd measure_temp)"
echo "throttled  : $(vcgencmd get_throttled)   (0x0 means no throttling occurred)"
echo
echo "Realtime factor under ~1.0x means transcription finishes before you could"
echo "have finished speaking it again -- the practical bar for dictation."
