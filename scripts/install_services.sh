#!/usr/bin/env bash
# Install voicekb as real systemd services that survive a reboot. Run ON THE PI.
#
#   bash scripts/install_services.sh            # install + enable + start
#   bash scripts/install_services.sh --uninstall
#
# Everything up to now has been started with `systemd-run`, which creates
# TRANSIENT units. Those are convenient for testing and they do NOT survive a
# reboot -- after a power cycle nothing comes back and the device looks dead.
# These are persistent unit files, enabled to start at boot.
#
# Three units, because they have genuinely different lifecycles:
#   voicekb-agent  the Bluetooth pairing agent (NoInputNoOutput)
#   voicekb-llm    llama-server holding the model in RAM
#   voicekb        the capture -> transcribe -> type pipeline
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
UNIT_DIR=/etc/systemd/system
UNITS=(voicekb.service voicekb-llm.service voicekb-agent.service)

if [[ "${1:-}" == "--uninstall" ]]; then
  for u in "${UNITS[@]}"; do
    sudo systemctl disable --now "$u" 2>/dev/null || true
    sudo rm -f "$UNIT_DIR/$u"
  done
  sudo systemctl daemon-reload
  echo "removed: ${UNITS[*]}"
  exit 0
fi

PY="$REPO_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "venv missing; run scripts/setup_pi.sh first" >&2; exit 1; }

# Stop any transient units left over from testing, or the enable below will
# collide with a unit of the same name that systemd-run already owns.
for u in "${UNITS[@]}"; do
  sudo systemctl stop "$u" 2>/dev/null || true
  sudo systemctl reset-failed "$u" 2>/dev/null || true
done

echo "=== voicekb-agent.service ==="
sudo tee "$UNIT_DIR/voicekb-agent.service" >/dev/null <<EOF
[Unit]
Description=voicekb Bluetooth pairing agent (NoInputNoOutput)
After=bluetooth.service
Requires=bluetooth.service

[Service]
# NoInputNoOutput makes hosts use "Just Works" pairing. Without it macOS and iOS
# both display a passkey and expect it typed on the keyboard being paired --
# impossible here, since typing needs HID channels that open only after pairing.
ExecStart=/usr/bin/bt-agent -c NoInputNoOutput
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "=== voicekb-llm.service ==="
sudo tee "$UNIT_DIR/voicekb-llm.service" >/dev/null <<EOF
[Unit]
Description=voicekb LLM server (llama.cpp)
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$REPO_ROOT
ExecStart=/bin/bash $REPO_ROOT/scripts/serve_llm.sh
Restart=on-failure
RestartSec=5
# Loading a ~1 GB model off the microSD is not instant.
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF

echo "=== voicekb.service ==="
sudo tee "$UNIT_DIR/voicekb.service" >/dev/null <<EOF
[Unit]
Description=voicekb speech-to-Bluetooth-keyboard pipeline
After=bluetooth.service sound.target voicekb-llm.service voicekb-agent.service
Requires=bluetooth.service
# Wants, not Requires, for the LLM: the pipeline degrades to typing the raw
# transcription if the server is absent, and refusing to dictate at all because
# reformatting is unavailable would be the worse failure.
Wants=voicekb-llm.service

[Service]
# Root is required to bind L2CAP PSMs 17 and 19.
User=root
WorkingDirectory=$REPO_ROOT
ExecStart=$PY -u $REPO_ROOT/scripts/run_voicekb.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
for u in "${UNITS[@]}"; do sudo systemctl enable "$u" >/dev/null; done
sudo systemctl start voicekb-agent.service voicekb-llm.service

echo "=== waiting for the model to load ==="
for i in $(seq 1 45); do
  curl -s -m 2 localhost:8080/health 2>/dev/null | grep -q ok && { echo "  llm ready (~$((i*2))s)"; break; }
  sleep 2
done
sudo systemctl start voicekb.service
sleep 3

echo
echo "=== status ==="
for u in "${UNITS[@]}"; do
  printf '  %-22s %-8s enabled=%s\n' "$u" "$(systemctl is-active "$u")" "$(systemctl is-enabled "$u" 2>/dev/null)"
done
echo
echo "These now start automatically at boot. Follow with:"
echo "  journalctl -u voicekb -f"
