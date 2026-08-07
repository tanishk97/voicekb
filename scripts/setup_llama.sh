#!/usr/bin/env bash
# Build llama.cpp and fetch small instruct models. Run this ON THE PI.
#
#   bash scripts/setup_llama.sh
#
# Two models are fetched so the accuracy/latency tradeoff can be measured rather
# than guessed, the same way the whisper model was chosen:
#   Llama-3.2-1B-Instruct   ~0.8 GB  faster
#   Qwen2.5-1.5B-Instruct   ~1.1 GB  better instruction-following
#
# Q4_K_M throughout: the usual sweet spot for CPU inference, roughly 4.5 bits
# per weight with much less quality loss than Q4_0.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$REPO_ROOT/vendor"
LLAMA="$VENDOR/llama.cpp"
MODELS="$REPO_ROOT/models"

echo "=== build dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential cmake git curl libcurl4-openssl-dev

mkdir -p "$VENDOR" "$MODELS"

if [[ -d "$LLAMA/.git" ]]; then
  echo "=== updating llama.cpp ==="
  git -C "$LLAMA" pull --ff-only
else
  echo "=== cloning llama.cpp ==="
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA"
fi

echo "=== building (slow: 4 cores, no active cooler) ==="
cmake -S "$LLAMA" -B "$LLAMA/build" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF >/dev/null
cmake --build "$LLAMA/build" -j"$(nproc)" --config Release --target llama-cli

BIN="$LLAMA/build/bin/llama-cli"
if [[ ! -x "$BIN" ]]; then
  echo "Build finished but $BIN is missing. Check the output above." >&2
  exit 1
fi
echo "Built: $BIN"

fetch() {
  local name="$1" url="$2" target="$MODELS/$1"
  if [[ -f "$target" ]]; then
    echo "  $name already present ($(du -h "$target" | cut -f1))"
    return
  fi
  echo "  fetching $name ..."
  curl -fL --progress-bar "$url" -o "$target"
  echo "  got $name ($(du -h "$target" | cut -f1))"
}

echo "=== models ==="
fetch "Llama-3.2-1B-Instruct-Q4_K_M.gguf" \
  "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf"
fetch "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf" \
  "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"

echo
echo "=== done ==="
echo "Compare them on real transcripts:"
echo "  ./.venv/bin/python scripts/bench_llm.py"
