#!/usr/bin/env bash
# Cap journald so logs cannot grow into the SD card. Run this ON THE PI.
#
#   bash scripts/setup_logging.sh
#
# Why this is needed: journald ships with every limit commented out, which means
# the built-in default applies -- SystemMaxUse is 10% of the filesystem, capped
# at 4 GB. On a 29 GB card that is ~2.9 GB of logs before anything is dropped.
# Not unbounded, but far more writing than an SD card should absorb for a device
# that logs a line per utterance, and SD cards fail from write wear.
#
# There is a privacy dimension too. voicekb logs the transcribed text of every
# utterance, so the journal accumulates a record of everything ever dictated --
# which on a dictation device could be anything. Retention is capped here; set
# `logging.log_transcripts: false` in config to stop recording the text itself.
set -euo pipefail

DROPIN_DIR=/etc/systemd/journald.conf.d
DROPIN=$DROPIN_DIR/10-voicekb.conf

if [[ "${1:-}" == "--revert" ]]; then
  sudo rm -f "$DROPIN"
  sudo systemctl restart systemd-journald
  echo "reverted to journald defaults"
  exit 0
fi

echo "=== before ==="
journalctl --disk-usage

sudo mkdir -p "$DROPIN_DIR"
sudo tee "$DROPIN" >/dev/null <<'EOF'
# Written by scripts/setup_logging.sh
[Journal]
# Hard ceiling on journal size. 200M is generous for a device that writes a
# handful of lines per utterance, and small enough to be irrelevant to SD wear.
SystemMaxUse=200M
# Keep some slack so a full journal can never be what fills the card.
SystemKeepFree=500M
# Individual files, so rotation happens in reasonable chunks.
SystemMaxFileSize=20M
# Drop anything older than a week. Dictation transcripts are useful for
# debugging today and a liability a month from now.
MaxRetentionSec=1week
# Bound the runtime (tmpfs) journal too, so a log storm cannot eat RAM.
RuntimeMaxUse=64M
EOF

sudo systemctl restart systemd-journald
# Apply the new ceiling to what is already on disk rather than waiting for it to
# be reached organically.
sudo journalctl --vacuum-size=200M >/dev/null 2>&1 || true
sudo journalctl --vacuum-time=1week >/dev/null 2>&1 || true

echo "=== after ==="
journalctl --disk-usage
echo
echo "Limits now in force:"
grep -E "^[A-Z]" "$DROPIN" | sed 's/^/  /'
echo
echo "Audio is never written to the card: scripts write WAVs under /tmp, which is"
echo "tmpfs (RAM-backed), and voicekb/stt.py uses a NamedTemporaryFile that is"
echo "deleted as soon as whisper has read it."
