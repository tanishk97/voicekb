#!/usr/bin/env bash
# Start llama-server with the model from config/default.yaml. Run ON THE PI.
#
#   bash scripts/serve_llm.sh                    # foreground
#   bash scripts/serve_llm.sh --service          # as a systemd unit
#   bash scripts/serve_llm.sh --stop
#   MODEL=models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf bash scripts/serve_llm.sh
#
# A resident server rather than llama-cli per utterance: reloading a ~1 GB model
# for every sentence would dominate latency, and the server applies each model's
# chat template from GGUF metadata instead of us hand-rolling it per model.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BIN="vendor/llama.cpp/build/bin/llama-server"
UNIT=voicekb-llm

if [[ "${1:-}" == "--stop" ]]; then
  sudo systemctl stop "$UNIT" 2>/dev/null || true
  sudo systemctl reset-failed "$UNIT" 2>/dev/null || true
  echo "stopped $UNIT"
  exit 0
fi

# Read model/threads/context from config so there is one source of truth.
read_cfg() {
  python3 - "$1" <<'PY'
import re, sys
key = sys.argv[1]
text = open("config/default.yaml").read()
block = text.split("\nllm:", 1)[1] if "\nllm:" in text else ""
m = re.search(rf"^\s+{key}:\s*(\S+)", block, re.M)
print(m.group(1) if m else "")
PY
}

MODEL="${MODEL:-$(read_cfg model)}"
THREADS="${THREADS:-$(read_cfg threads)}"
CTX="${CTX:-$(read_cfg context_size)}"
PORT="${PORT:-8080}"

[[ -n "$MODEL" ]] || { echo "Could not read llm.model from config" >&2; exit 1; }
if [[ ! -x "$BIN" ]]; then
  echo "$BIN not built. Run: bash scripts/setup_llama.sh" >&2
  exit 1
fi
if [[ ! -f "$MODEL" ]]; then
  echo "Model not found: $MODEL. Run: bash scripts/setup_llama.sh" >&2
  exit 1
fi

echo "model   : $MODEL ($(du -h "$MODEL" | cut -f1))"
echo "threads : $THREADS   context: $CTX   port: $PORT"

# Bind to loopback only. This is an unauthenticated inference endpoint and the
# Pi sits on a home network; there is no reason to expose it beyond localhost.
ARGS=(-m "$MODEL" -t "$THREADS" -c "$CTX" --host 127.0.0.1 --port "$PORT")

if [[ "${1:-}" == "--service" ]]; then
  sudo systemctl stop "$UNIT" 2>/dev/null || true
  sudo systemctl reset-failed "$UNIT" 2>/dev/null || true
  sudo systemd-run --unit="$UNIT" --working-directory="$REPO_ROOT" \
    "$REPO_ROOT/$BIN" "${ARGS[@]}"
  echo "started $UNIT; follow with: journalctl -u $UNIT -f"
  echo "health:  curl -s localhost:$PORT/health"
else
  exec "$BIN" "${ARGS[@]}"
fi
