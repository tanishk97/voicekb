#!/usr/bin/env bash
# Stage 1 dependencies. Run this ON THE PI.
#
#   bash scripts/setup_pi.sh
set -euo pipefail

echo "=== apt packages ==="
sudo apt update
# alsa-utils    -> arecord/aplay/amixer, for verifying capture outside Python
# libportaudio2 -> the backend sounddevice binds to
# python3-venv  -> Pi OS Lite ships python3 without venv
# python3-dbus/python3-gi are needed by voicekb/bt_hid.py to register the HID
# SDP record over BlueZ's D-Bus API. They are apt-only (no usable wheels), so
# the venv is created with --system-site-packages to see them.
sudo apt install -y alsa-utils libportaudio2 python3-venv python3-dev git \
  python3-dbus python3-gi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== python venv ==="
# --system-site-packages so a piwheels-built numpy can be reused if present;
# building numpy from source on a Pi is slow.
[[ -d .venv ]] || python3 -m venv --system-site-packages .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo
echo "=== done ==="
echo "Next:"
echo "  ./.venv/bin/python scripts/check_mic.py --list"
echo "  bash scripts/set_gain.sh"
echo "  ./.venv/bin/python scripts/check_mic.py"
