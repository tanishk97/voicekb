#!/usr/bin/env bash
# Push the working tree to the Pi. Code is edited on the Mac and runs on the Pi.
#
#   PI_HOST=pi@192.168.12.42 scripts/sync_to_pi.sh
#
# Set PI_HOST once in your shell profile to avoid repeating it.
set -euo pipefail

PI_HOST="${PI_HOST:-}"
PI_PATH="${PI_PATH:-~/AiMicrophone}"

if [[ -z "$PI_HOST" ]]; then
  echo "PI_HOST is not set. Example:" >&2
  echo "  PI_HOST=pi@192.168.12.42 scripts/sync_to_pi.sh" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rsync -av --delete \
  --exclude '.git/' \
  --exclude '.venv*/' \
  --exclude 'models/' \
  --exclude '__pycache__/' \
  --exclude '*.wav' \
  "$REPO_ROOT/" "$PI_HOST:$PI_PATH/"

echo
echo "Synced to $PI_HOST:$PI_PATH"
