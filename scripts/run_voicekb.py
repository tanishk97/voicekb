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
from voicekb.llm import LlamaReformatter  # noqa: E402
from voicekb.text import (  # noqa: E402
    apply_substitutions,
    is_non_speech,
    normalize_for_hid,
    strip_fillers,
)
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
    llm: LlamaReformatter | None,
    profile: str,
    substitutions: dict[str, str],
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

        # Non-speech is decided on whisper's raw output, before the LLM sees it.
        # Handing "(crickets chirping)" to a reformatter just invites it to
        # invent a sentence about crickets.
        if is_non_speech(result.text):
            _log(f"  {secs:.1f}s -> non-speech, ignored (raw: {result.text!r})")
            continue

        # Deterministic filler removal for every profile except 'raw', which
        # promises the exact transcription. It cannot hallucinate or delete
        # anything that was not on the filler list.
        # Known mis-transcriptions first: fix the words, then remove filler,
        # so a substituted term is present before anything else reasons about it.
        text = apply_substitutions(result.text, substitutions)
        if text != result.text:
            _log(f"  substituted: {result.text!r} -> {text!r}")
        before_fillers = text
        if profile != "raw":
            text = strip_fillers(text)
        if text != before_fillers:
            _log(f"  fillers: {before_fillers!r} -> {text!r}")
        if llm is not None:
            try:
                shaped = llm.reformat(text, profile)
                # Log all three outcomes. Logging only changes made "no line at
                # all" mean either "left it alone" or "rewrite rejected", which
                # are very different and indistinguishable in the journal.
                if not shaped.elapsed_seconds and not shaped.changed:
                    pass  # profile does not use the model; nothing to report
                elif shaped.rejected:
                    _log(f"  llm[{profile}] {shaped.elapsed_seconds:.1f}s "
                         f"REJECTED (overlap {shaped.overlap:.2f}); typing raw")
                elif shaped.changed:
                    _log(f"  llm[{profile}] {shaped.elapsed_seconds:.1f}s "
                         f"(overlap {shaped.overlap:.2f}): {text!r} -> {shaped.text!r}")
                else:
                    _log(f"  llm[{profile}] {shaped.elapsed_seconds:.1f}s "
                         "left it unchanged")
                text = shaped.text
            except Exception as exc:  # noqa: BLE001
                # Type the raw transcription rather than losing the utterance.
                _log(f"  llm failed, using raw transcription: {exc}")

        # Normalize AFTER the LLM, not before: models emit curly quotes and em
        # dashes enthusiastically, and those have no HID keycodes.
        text = normalize_for_hid(text)
        if not text:
            _log(f"  {secs:.1f}s -> nothing left to type after normalization")
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
    ap.add_argument("--profile", default=None,
                    help="clean | slack | commit | email | raw "
                         "(overrides llm.profile in config)")
    ap.add_argument("--no-llm", action="store_true",
                    help="type the raw transcription, bypassing the LLM")
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

    # Reformatting layer. AI-cleaned is the default mode per the design; "raw"
    # is the deliberate fallback rather than the starting point.
    profile = args.profile or cfg.llm.profile
    llm: LlamaReformatter | None = None
    if cfg.llm.enabled and not args.no_llm and profile != "raw":
        llm = LlamaReformatter(
            base_url=cfg.llm.base_url,
            timeout_s=cfg.llm.timeout_s,
            vocabulary=list(cfg.llm.vocabulary),
        )
        if not llm.available():
            if not cfg.llm.fallback_to_raw:
                _log(f"FATAL: no llama-server at {cfg.llm.base_url}")
                _log("  start it with: bash scripts/serve_llm.sh --service")
                return 1
            # Degrade to raw rather than refusing to run. A dictation device
            # that types nothing is worse than one that types unpolished text.
            _log(f"WARNING: no llama-server at {cfg.llm.base_url}; "
                 "typing raw transcription")
            _log("  start it with: bash scripts/serve_llm.sh --service")
            llm = None
    _log(f"profile: {profile}" + ("" if llm is not None else "  (LLM bypassed)"))

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
        args=(work, stt, kb, sr, not args.no_trailing_space, args.dry_run,
              llm, profile, dict(cfg.stt.substitutions)),
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
                # Try dialling the host back before falling back to waiting.
                # macOS keeps the baseband ACL link open and believes it is still
                # connected, so it never reopens the L2CAP channels -- leaving us
                # stuck in accept() while the Mac shows a live connection. A real
                # keyboard initiates its own reconnection; so do we now.
                _log(f"host disconnected; dialling {host} back ...")
                host = kb.connect_or_accept(host)
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
