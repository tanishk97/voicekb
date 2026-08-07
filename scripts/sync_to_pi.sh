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

# --delete keeps the Pi a mirror of the repo, which means anything present only
# on the Pi is destroyed. Build outputs and model weights live only on the Pi, so
# the exclude list is load-bearing, not cosmetic.
#
# It is driven from .gitignore rather than a hand-maintained list: a second copy
# of that list will drift, and the failure mode is silent destruction of a
# multi-minute build. (rsync does not delete excluded files unless you also pass
# --delete-excluded, which we deliberately do not.)
rsync -av --delete \
  --filter=':- .gitignore' \
  --exclude '.git/' \
  --exclude 'vendor/' \
  --exclude 'models/' \
  "$REPO_ROOT/" "$PI_HOST:$PI_PATH/"

echo
echo "Synced to $PI_HOST:$PI_PATH"
