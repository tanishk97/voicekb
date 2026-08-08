#!/usr/bin/env bash
# Open a pairing window so a NEW device can find the Pi. Run this ON THE PI.
#
#   bash scripts/pair_mode.sh          # discoverable for 300s, then closes
#   bash scripts/pair_mode.sh 600      # ...for 10 minutes
#   bash scripts/pair_mode.sh --off    # close it now
#   bash scripts/pair_mode.sh --status
#
# You only need this for a device that has never been paired. Devices already in
# the paired list reconnect without discovery -- if one of those cannot connect,
# discovery is not the problem and `journalctl -u voicekb -f` is where to look.
#
# WHY THIS IS A WINDOW AND NOT ALWAYS ON
#
# Pairing uses a NoInputNoOutput agent, which means "Just Works" pairing with no
# passkey to confirm. That is necessary -- a keyboard cannot display a code, and
# it cannot type one before it is paired. But it also means anyone in Bluetooth
# range who sees the device can pair with it unchallenged.
#
# This device TYPES WHAT YOU SAY to whatever host is connected. A stranger who
# pairs while you are dictating receives your words. Leaving it permanently
# discoverable turns a private dictation device into one that will introduce
# itself to the room.
#
# So: open the window when you are pairing something, and let it close.
set -euo pipefail

if ! command -v bluetoothctl >/dev/null 2>&1; then
  echo "bluetoothctl not found. Install with: sudo apt install -y bluez" >&2
  exit 1
fi

state() {
  printf 'show\nquit\n' | bluetoothctl 2>/dev/null \
    | grep -E 'Alias:|Powered:|Discoverable:|Pairable:' | sed 's/^\s*/  /'
}

case "${1:-}" in
  --status)
    state
    echo "  --- paired devices ---"
    printf 'devices Paired\nquit\n' | bluetoothctl 2>/dev/null \
      | grep -i '^Device' | sed 's/^/  /' || echo "  (none)"
    exit 0
    ;;
  --off)
    sudo bluetoothctl discoverable off >/dev/null 2>&1 || true
    echo "pairing window closed"
    state
    exit 0
    ;;
esac

WINDOW="${1:-300}"

# The agent must be running or the host falls back to the standard keyboard
# pairing flow: display a passkey and expect it typed on the keyboard being
# paired -- impossible here, since typing needs HID channels that only open
# after pairing completes. Both macOS and iOS hit this when it was absent.
if ! systemctl is-active --quiet voicekb-agent; then
  echo "WARNING: voicekb-agent is not running." >&2
  echo "  Without it the host will ask for a passkey you cannot type." >&2
  echo "  Start it with: sudo systemctl start voicekb-agent" >&2
  echo >&2
fi

sudo bluetoothctl pairable on >/dev/null 2>&1 || true
sudo bluetoothctl discoverable on >/dev/null 2>&1 || true

echo "=== discoverable for ${WINDOW}s ==="
state
echo
echo "On the other device: Bluetooth settings -> look for 'voicekb' -> connect."
echo "It should not ask for a code. If it does, the agent is not running."
echo
echo "Ctrl-C leaves the window open; it closes on its own after ${WINDOW}s."

sleep "$WINDOW"
sudo bluetoothctl discoverable off >/dev/null 2>&1 || true
echo "window closed."
