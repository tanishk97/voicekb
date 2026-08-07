#!/usr/bin/env bash
# Build whisper.cpp and fetch models. Run this ON THE PI.
#
#   bash scripts/setup_whisper.sh                 # base.en + small.en, both q5_1
#   bash scripts/setup_whisper.sh base.en-q5_1    # just one
#
# Built from source rather than pip-installed because we want the ARM NEON /
# fp16 paths the Pi 5's Cortex-A76 cores provide, and control over thread count.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$REPO_ROOT/vendor"
WHISPER="$VENDOR/whisper.cpp"
MODELS="$REPO_ROOT/models"

MODELS_WANTED=("$@")
if [[ ${#MODELS_WANTED[@]} -eq 0 ]]; then
  # q5_1 rather than fp16: roughly half the size and faster on CPU, with
  # accuracy loss that is negligible for dictation.
  MODELS_WANTED=(base.en-q5_1 small.en-q5_1)
fi

echo "=== build dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential cmake git

mkdir -p "$VENDOR" "$MODELS"

if [[ -d "$WHISPER/.git" ]]; then
  echo "=== updating whisper.cpp ==="
  git -C "$WHISPER" pull --ff-only
else
  echo "=== cloning whisper.cpp ==="
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER"
fi

echo "=== building (this is the slow part; ~4 cores, no active cooler) ==="
cmake -S "$WHISPER" -B "$WHISPER/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$WHISPER/build" -j"$(nproc)" --config Release

BIN="$WHISPER/build/bin/whisper-cli"
if [[ ! -x "$BIN" ]]; then
  echo "Build finished but $BIN is missing. Check the build output above." >&2
  exit 1
fi
echo "Built: $BIN"

echo "=== models ==="
for m in "${MODELS_WANTED[@]}"; do
  target="$MODELS/ggml-${m}.bin"
  if [[ -f "$target" ]]; then
    echo "  $m already present ($(du -h "$target" | cut -f1))"
    continue
  fi
  echo "  fetching $m ..."
  curl -fL --progress-bar \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-${m}.bin" \
    -o "$target"
  echo "  got $m ($(du -h "$target" | cut -f1))"
done

echo
echo "=== done ==="
echo "Benchmark against a real recording:"
echo "  bash scripts/bench_whisper.sh /tmp/speech.wav"
