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

# One core is left free deliberately. With no active cooler, -j4 pushed the SoC
# to 72 C while the pipeline was also running, and small.en alone has tripped the
# 80 C soft limit before. Peak temperature is the binding constraint here, not
# total build time. Override with JOBS=4 if you have added cooling.
JOBS="${JOBS:-$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))}"

echo "=== building with $JOBS job(s); watch the temperature ==="
cmake -S "$LLAMA" -B "$LLAMA/build" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF >/dev/null
# llama-server, not just llama-cli: voicekb/llm.py talks to the server's
# OpenAI-compatible API so the model stays resident and chat templates are
# applied from GGUF metadata rather than hand-rolled per model.
cmake --build "$LLAMA/build" -j"$JOBS" --config Release --target llama-cli llama-server

for want in llama-cli llama-server; do
  if [[ ! -x "$LLAMA/build/bin/$want" ]]; then
    echo "Build finished but $want is missing. Check the output above." >&2
    exit 1
  fi
  echo "Built: $LLAMA/build/bin/$want"
done
echo "SoC temperature after build: $(vcgencmd measure_temp 2>/dev/null || echo n/a)"

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
