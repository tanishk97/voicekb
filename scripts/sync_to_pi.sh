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

# --delete keeps the Pi a mirror of the repo, which means anything living only on
# the Pi is destroyed unless explicitly spared. The venv, the whisper.cpp build,
# and the model weights all live only on the Pi and cost minutes to rebuild.
#
# Each of those gets TWO rules, deliberately:
#   P  protect from deletion at the destination
#   -  exclude from transfer
#
# An earlier version relied on `--filter=':- .gitignore'` to cover this. It did
# not: `.venv/` was listed in .gitignore and got deleted anyway, leaving only the
# `__pycache__` directories that happened to match a different rule. Inferring
# deletion safety from gitignore semantics is too subtle to pair with --delete,
# so these are spelled out.
#
# The leading slash anchors each pattern to the transfer root, so a stray
# directory of the same name deeper in the tree is not silently spared.
rsync -av --delete \
  --filter='P /.venv/' --filter='- /.venv/' \
  --filter='P /.venv-dev/' --filter='- /.venv-dev/' \
  --filter='P /vendor/' --filter='- /vendor/' \
  --filter='P /models/' --filter='- /models/' \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.wav' \
  "$REPO_ROOT/" "$PI_HOST:$PI_PATH/"

echo
echo "Synced to $PI_HOST:$PI_PATH"
