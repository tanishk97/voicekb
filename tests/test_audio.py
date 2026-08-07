#!/usr/bin/env python3
"""Tests for the audio layer. No pytest — runs anywhere, including the Pi.

    python3 tests/test_audio.py

These cover the pure logic only; they need no microphone, so they are safe to
run on any machine and are the first thing to check when capture misbehaves on
hardware. If these pass and the mic still sounds wrong, the bug is in the
device/ALSA layer, not here.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.audio import apply_gain, dbfs, peak_dbfs, to_mono  # noqa: E402
from voicekb.config import AudioConfig, Config  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


def test_config() -> None:
    print("=== config ===")
    cfg = Config.load().audio
    check("frame_samples is 480 at 16k/30ms", cfg.frame_samples == 480, f"got {cfg.frame_samples}")
    check("downstream contract: 16 kHz mono", cfg.sample_rate == 16000 and cfg.channels >= 1)

    # webrtcvad only accepts 10/20/30 ms frames; catching this at config load
    # beats a confusing failure deep in the VAD later.
    try:
        AudioConfig(frame_ms=25)
        check("rejects frame_ms webrtcvad cannot use", False)
    except ValueError:
        check("rejects frame_ms webrtcvad cannot use", True)

    try:
        AudioConfig(channels=1, channel_select=3)
        check("rejects channel_select outside channel count", False)
    except ValueError:
        check("rejects channel_select outside channel count", True)


def test_levels() -> None:
    print("=== level metering ===")
    check("digital silence reads -inf", dbfs(np.zeros(480, np.int16)) == -math.inf)
    full = np.full(480, 32767, np.int16)
    check("full scale is ~0 dBFS", abs(dbfs(full)) < 0.01, f"{dbfs(full):.4f}")
    half = np.full(480, 16384, np.int16)
    check("half scale is ~-6 dBFS", abs(dbfs(half) + 6.02) < 0.05, f"{dbfs(half):.2f}")
    sine = (np.sin(np.linspace(0, 20 * np.pi, 480)) * 20000).astype(np.int16)
    check("peak exceeds RMS on a sine", peak_dbfs(sine) > dbfs(sine))
    check("empty frame does not crash", dbfs(np.array([], np.int16)) == -math.inf)


def test_channels() -> None:
    """The mic-array swap path: these must hold before trusting a multichannel device."""
    print("=== channel collapse ===")
    stereo = np.array([[100, 300]] * 10, np.int16)
    check("averages when channel_select is None", to_mono(stereo, None)[0] == 200)
    check("picks channel 0", to_mono(stereo, 0)[0] == 100)
    check("picks channel 1", to_mono(stereo, 1)[0] == 300)
    # Averaging in int16 would wrap here; audio.py promotes to int32 first.
    loud = np.array([[32000, 32000]] * 4, np.int16)
    check("no int16 wrap when averaging loud channels", to_mono(loud, None)[0] == 32000,
          f"got {to_mono(loud, None)[0]}")
    check("already-mono passes through", to_mono(np.zeros(10, np.int16), None).ndim == 1)


def test_gain() -> None:
    print("=== software gain ===")
    frame = np.full(10, 16384, np.int16)
    check("gain of 1.0 avoids a copy", apply_gain(frame, 1.0) is frame)
    # Overflow must clip, not wrap -- a wrap turns a loud vowel into noise.
    check("clips positive overflow", apply_gain(np.full(10, 30000, np.int16), 4.0)[0] == 32767)
    check("clips negative overflow", apply_gain(np.full(10, -30000, np.int16), 4.0)[0] == -32768)


def main() -> int:
    for fn in (test_config, test_levels, test_channels, test_gain):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
