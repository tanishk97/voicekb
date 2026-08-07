#!/usr/bin/env bash
# Configure BlueZ so the Pi can act as a Bluetooth HID *keyboard*. Run ON THE PI.
#
#   bash scripts/setup_bluetooth.sh
#
# Two changes, both necessary:
#
# 1. Class of Device -> 0x002540 (Peripheral / Keyboard). macOS decides what
#    icon and pairing flow to use from this. Left at the default, the Mac treats
#    the Pi as a generic computer and will not offer to use it as a keyboard.
#
# 2. bluetoothd is started with --noplugin=input. BlueZ's built-in `input`
#    plugin implements the HID *Host* role and binds L2CAP PSMs 17 and 19 --
#    the exact two our HID *Device* needs to listen on. With the plugin loaded
#    our bind() fails with EADDRINUSE.
#
# Both are reverted by scripts/setup_bluetooth.sh --revert.
set -euo pipefail

MAIN_CONF=/etc/bluetooth/main.conf
OVERRIDE_DIR=/etc/systemd/system/bluetooth.service.d
OVERRIDE=$OVERRIDE_DIR/10-hid-device.conf
# Probed with a loop rather than `ls a b | head -1`: ls exits 2 when any argument
# is missing, and under `set -e` with `pipefail` that aborts the script silently
# before a single line of output.
BLUETOOTHD=""
for candidate in /usr/libexec/bluetooth/bluetoothd /usr/lib/bluetooth/bluetoothd; do
  if [[ -x "$candidate" ]]; then BLUETOOTHD="$candidate"; break; fi
done
KEYBOARD_CLASS=0x002540

if [[ "${1:-}" == "--revert" ]]; then
  echo "=== reverting ==="
  sudo rm -f "$OVERRIDE"
  sudo sed -i 's/^Class = 0x002540/#Class = 0x000100/' "$MAIN_CONF" || true
  sudo systemctl daemon-reload
  sudo systemctl restart bluetooth
  echo "reverted; bluetoothd restarted with defaults"
  exit 0
fi

if [[ -z "$BLUETOOTHD" ]]; then
  echo "Could not find the bluetoothd binary." >&2
  exit 1
fi

echo "=== 1. Class of Device -> $KEYBOARD_CLASS (keyboard) ==="
sudo cp -n "$MAIN_CONF" "$MAIN_CONF.bak" 2>/dev/null || true
if grep -qE '^Class = ' "$MAIN_CONF"; then
  sudo sed -i "s|^Class = .*|Class = $KEYBOARD_CLASS|" "$MAIN_CONF"
else
  # The stock file ships it commented out under [General].
  sudo sed -i "s|^#Class = .*|Class = $KEYBOARD_CLASS|" "$MAIN_CONF"
fi
grep -E '^Class' "$MAIN_CONF" || {
  echo "Failed to set Class in $MAIN_CONF" >&2; exit 1; }

echo "=== 2. start bluetoothd with --noplugin=input ==="
sudo mkdir -p "$OVERRIDE_DIR"
sudo tee "$OVERRIDE" >/dev/null <<EOF
# Written by scripts/setup_bluetooth.sh
# The built-in input plugin is the HID Host role and occupies L2CAP PSM 17/19,
# which our HID Device needs. Empty ExecStart= first clears the unit's original.
[Service]
ExecStart=
ExecStart=$BLUETOOTHD --noplugin=input
EOF

sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sleep 2

echo "=== verify ==="
echo "running: $(systemctl show -p ExecStart --value bluetooth | grep -oE '\-\-noplugin=[a-z]*' || echo '(no --noplugin flag!)')"
echo "class  : $(hciconfig hci0 class 2>/dev/null | tail -1 | tr -d ' \t')"
systemctl is-active bluetooth

echo
echo "Now register the HID profile and listen:"
echo "  sudo ./.venv/bin/python -m voicekb.bt_hid --serve"
