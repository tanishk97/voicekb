#!/usr/bin/env python3
"""Run VAD + whisper + normalization over a WAV file. No mic, no Bluetooth.

    ./.venv/bin/python scripts/transcribe_file.py /tmp/speech.wav

This is the offline half of the pipeline, so segmentation and transcription can
be tuned against a fixed recording instead of by talking at the mic repeatedly.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.config import DEFAULT_CONFIG, Config  # noqa: E402
from voicekb.hid_keycodes import unmappable  # noqa: E402
from voicekb.stt import WhisperSTT  # noqa: E402
from voicekb.text import normalize_for_hid  # noqa: E402
from voicekb.vad import VadSegmenter  # noqa: E402


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expected 16-bit mono WAV")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav", type=Path)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--no-vad", action="store_true",
                    help="transcribe the whole file as one utterance")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    audio, sr = load_wav(args.wav)
    print(f"input: {args.wav}  ({len(audio) / sr:.2f}s @ {sr} Hz)")

    if args.no_vad:
        utterances = [audio]
        print("VAD: skipped")
    else:
        seg = VadSegmenter(cfg.vad, sr, cfg.audio.frame_ms)
        n = cfg.audio.frame_samples
        utterances = []
        for i in range(0, len(audio) - n + 1, n):
            u = seg.push(audio[i:i + n])
            if u is not None:
                utterances.append(u)
        tail = seg.flush()
        if tail is not None:
            utterances.append(tail)
        total = sum(len(u) for u in utterances) / sr
        print(f"VAD: {len(utterances)} utterance(s), {total:.2f}s of "
              f"{len(audio) / sr:.2f}s kept "
              f"({', '.join(f'{len(u) / sr:.2f}s' for u in utterances) or '-'})")

    if not utterances:
        print("\nNo speech detected. Lower vad.aggressiveness or check the recording.")
        return 2

    stt = WhisperSTT(cfg.stt)
    for i, u in enumerate(utterances, 1):
        t = stt.transcribe(u, sr)
        clean = normalize_for_hid(t.text)
        left = unmappable(clean)
        print(f"\n[{i}] {t.elapsed_seconds:.2f}s for {t.audio_seconds:.2f}s audio "
              f"({t.realtime_factor:.2f}x realtime)")
        print(f"    raw       : {t.text!r}")
        print(f"    normalized: {clean!r}")
        if left:
            print(f"    WARNING untypeable characters remain: {left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
