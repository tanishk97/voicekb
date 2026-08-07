#!/usr/bin/env python3
"""The end-to-end loop: speak into the Pi, watch text appear on the Mac.

    sudo ./.venv/bin/python scripts/run_voicekb.py

Runs as root because binding L2CAP PSMs 17 and 19 is privileged.

Transcription happens on a worker thread rather than inline. whisper blocks for
roughly 2.4s per utterance, and if that ran on the capture thread the audio
queue would overflow and drop whatever you said while it was thinking -- so
speaking two sentences in a row would silently lose the second one.
"""

from __future__ import annotations

import argparse
import queue
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.audio import open_source  # noqa: E402
from voicekb.bt_hid import BluetoothHIDKeyboard  # noqa: E402
from voicekb.config import DEFAULT_CONFIG, Config  # noqa: E402
from voicekb.stt import WhisperSTT  # noqa: E402
from voicekb.text import normalize_for_hid  # noqa: E402
from voicekb.vad import VadSegmenter  # noqa: E402

_stop = threading.Event()
# Set by the worker when a write to the HID channel fails, so the capture loop
# can drop back to accept() and wait for the host to come back.
_disconnected = threading.Event()


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def transcribe_worker(
    work: queue.Queue,
    stt: WhisperSTT,
    kb: BluetoothHIDKeyboard,
    sample_rate: int,
    trailing_space: bool,
    dry_run: bool,
) -> None:
    while not _stop.is_set():
        item = work.get()
        if item is None:
            return
        audio = item
        secs = len(audio) / sample_rate
        try:
            result = stt.transcribe(audio, sample_rate)
        except Exception as exc:  # noqa: BLE001
            _log(f"  transcription failed: {exc}")
            continue

        text = normalize_for_hid(result.text)
        if not text:
            # Either silence or a sound description like "(swooshing)".
            _log(f"  {secs:.1f}s -> non-speech, ignored (raw: {result.text!r})")
            continue

        _log(f"  {secs:.1f}s -> {result.elapsed_seconds:.1f}s -> {text!r}")
        if dry_run:
            continue
        try:
            skipped = kb.type_text(text + (" " if trailing_space else ""))
            if skipped:
                _log(f"  note: could not type {skipped}")
        except Exception as exc:  # noqa: BLE001
            # The Mac went away (sleep, out of range, Bluetooth toggled). Signal
            # the capture loop to drop back to accept() rather than dying: a
            # stale half-open link is exactly what made the first end-to-end
            # attempt look like a total failure.
            _log(f"  typing failed, assuming host disconnected: {exc}")
            _disconnected.set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run", action="store_true",
                    help="transcribe and log but never type; no Bluetooth needed")
    ap.add_argument("--no-trailing-space", action="store_true")
    ap.add_argument("--delay-ms", type=float, default=15.0)
    args = ap.parse_args()

    cfg = Config.load(args.config)
    sr = cfg.audio.sample_rate

    _log("loading whisper ...")
    stt = WhisperSTT(cfg.stt)

    # Prove the mic opens BEFORE asking the user to connect Bluetooth. Otherwise
    # an audio problem only surfaces after pairing, which looks like a Bluetooth
    # failure and sends you debugging the wrong subsystem. (This is exactly how
    # the /etc/asound.conf-vs-~/.asoundrc bug presented: the daemon accepted the
    # HID connection, then died on the mic.)
    _log(f"checking mic {cfg.audio.device!r} ...")
    try:
        probe = open_source(cfg.audio)
        probe.start()
        probe.stop()
    except Exception as exc:  # noqa: BLE001
        _log(f"FATAL: cannot open audio device {cfg.audio.device!r}: {exc}")
        _log("  list devices with: ./.venv/bin/python scripts/check_mic.py --list")
        _log("  if running as root, the device must be in /etc/asound.conf, not "
             "~/.asoundrc -- run scripts/setup_alsa.sh")
        return 1
    _log("  mic OK")

    kb = BluetoothHIDKeyboard(key_delay_s=args.delay_ms / 1000.0)
    if not args.dry_run:
        _log("registering HID profile ...")
        kb.register_profile()
        kb.listen()
        _log("waiting for your Mac to connect (Bluetooth menu -> voicekb) ...")
        host = kb.accept()
        _log(f"connected to {host}")
        time.sleep(1.0)  # let the host's HID stack settle before the first report

    work: queue.Queue = queue.Queue(maxsize=8)
    worker = threading.Thread(
        target=transcribe_worker,
        args=(work, stt, kb, sr, not args.no_trailing_space, args.dry_run),
        daemon=True,
    )
    worker.start()

    def handle_signal(_sig, _frm):  # noqa: ANN001
        _stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    seg = VadSegmenter(cfg.vad, sr, cfg.audio.frame_ms)
    _log(f"listening. speak into the mic. (vad aggressiveness={cfg.vad.aggressiveness}, "
         f"silence={cfg.vad.silence_ms}ms)")
    _log("Ctrl-C to stop.")

    utterances = 0
    try:
        with open_source(cfg.audio) as source:
            frames = source.frames()
            while not _stop.is_set():
                for frame in frames:
                    if _stop.is_set() or _disconnected.is_set():
                        break
                    done = seg.push(frame)
                    if done is not None:
                        utterances += 1
                        _log(f"utterance {utterances}: {len(done) / sr:.1f}s captured")
                        try:
                            work.put_nowait(done)
                        except queue.Full:
                            _log("  dropped: transcription is falling behind")

                if _stop.is_set() or args.dry_run:
                    break
                # Host vanished mid-session. Wait for it to come back instead of
                # exiting, so sleeping the Mac or wandering out of range does not
                # require restarting the service.
                _disconnected.clear()
                seg = VadSegmenter(cfg.vad, sr, cfg.audio.frame_ms)
                _log("host disconnected; waiting for it to reconnect ...")
                host = kb.accept()
                _log(f"reconnected to {host}")
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        try:
            work.put_nowait(None)
        except queue.Full:
            pass
        kb.close()
        _log(f"stopped after {utterances} utterance(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
