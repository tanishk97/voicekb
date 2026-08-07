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

from voicekb.audio import HighPassFilter, apply_gain, dbfs, peak_dbfs, to_mono  # noqa: E402
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


def _tone(freq: float, secs: float = 0.5, sr: int = 16000, amp: int = 10000) -> np.ndarray:
    t = np.arange(int(sr * secs)) / sr
    return (np.sin(2 * np.pi * freq * t) * amp).astype(np.int16)


def test_highpass() -> None:
    print("=== high-pass filter ===")
    sr = 16000

    def response_db(freq: float) -> float:
        hpf = HighPassFilter(80.0, sr)
        sig = _tone(freq, 0.5, sr)
        out = hpf.process(sig)
        # Skip the settling transient at the start.
        return dbfs(out[sr // 10:]) - dbfs(sig[sr // 10:])

    at_1k = response_db(1000.0)
    at_300 = response_db(300.0)
    at_40 = response_db(40.0)
    check("1 kHz passes ~untouched", abs(at_1k) < 0.5, f"{at_1k:+.2f} dB")
    check("300 Hz mostly passes", at_300 > -2.0, f"{at_300:+.2f} dB")
    # Second-order rolloff is 12 dB/octave; 40 Hz is one octave below the 80 Hz
    # corner, so expect roughly -12 dB or better.
    check("40 Hz strongly attenuated", at_40 < -10.0, f"{at_40:+.2f} dB")
    check("rolloff is monotonic", at_40 < at_300 < at_1k + 0.5)

    # DC offset is what a plosive thump looks like at the extreme.
    hpf = HighPassFilter(80.0, sr)
    dc = np.full(sr // 2, 5000, np.int16)
    out = hpf.process(dc)
    check("DC offset removed", abs(int(out[-1])) < 50, f"tail={int(out[-1])}")

    # State must persist across frames, or every frame boundary is a click.
    sig = _tone(200.0, 0.3, sr)
    whole = HighPassFilter(80.0, sr).process(sig)
    chunked_f = HighPassFilter(80.0, sr)
    chunked = np.concatenate([chunked_f.process(sig[i:i + 480])
                              for i in range(0, sig.size, 480)])
    check("chunked output matches single-pass (state carries)",
          np.array_equal(whole[:chunked.size], chunked),
          f"max diff {int(np.max(np.abs(whole[:chunked.size].astype(np.int32) - chunked.astype(np.int32))))}")

    # Disabled must be a true passthrough.
    off = HighPassFilter(0.0, sr)
    check("cutoff 0 disables the filter", off.process(sig) is sig)

    try:
        HighPassFilter(9000.0, sr)
        check("rejects cutoff above Nyquist", False)
    except ValueError:
        check("rejects cutoff above Nyquist", True)


def main() -> int:
    for fn in (test_config, test_levels, test_channels, test_gain, test_highpass):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
