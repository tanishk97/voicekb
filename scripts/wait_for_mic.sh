#!/usr/bin/env bash
# Block until an ALSA capture card exists, or fail after a timeout.
#
# Used as ExecStartPre for voicekb.service. sound.target fires before USB
# probing finishes, so on a cold boot the pipeline raced the microphone and
# restarted four times before catching it. Restart=on-failure papered over that,
# but a service that appears to fail four times every boot makes its own logs
# untrustworthy.
#
# This lives in a file rather than inline in the unit because systemd treats
# `$` and `%` as specifiers, and an inline `$(seq ...)` produces
# "bad unit file setting" rather than anything that hints at the real cause.
set -uo pipefail
TIMEOUT="${1:-30}"

for _ in $(seq 1 "$TIMEOUT"); do
  if arecord -l 2>/dev/null | grep -q '^card'; then
    exit 0
  fi
  sleep 1
done

echo "no ALSA capture card appeared within ${TIMEOUT}s" >&2
exit 1
