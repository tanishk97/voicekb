#!/usr/bin/env bash
# Wait for llama-server to answer, but never block dictation on it.
#
# Used as ExecStartPre for voicekb.service. Both units start together and the
# model takes several seconds to load, so without this the first utterance after
# a boot is transcribed raw.
#
# Always exits 0. The microphone is the product; a missing reformatting server
# must never stop the device from typing what you said.
set -uo pipefail
TIMEOUT="${1:-45}"
URL="${2:-http://127.0.0.1:8080/health}"

for _ in $(seq 1 "$TIMEOUT"); do
  if curl -s -m 2 "$URL" 2>/dev/null | grep -q '"ok"'; then
    echo "llama-server ready"
    exit 0
  fi
  sleep 1
done
echo "llama-server not ready after ${TIMEOUT}s; starting anyway (will reformat once it is)" >&2
exit 0
