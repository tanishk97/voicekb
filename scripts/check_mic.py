#!/usr/bin/env python3
"""Stage 1 verification: is the mic detected, and is it capturing usable audio?

  python3 scripts/check_mic.py --list          # enumerate capture devices
  python3 scripts/check_mic.py                 # record 5s and grade the signal

Run the recording test twice: once silent (to measure the noise floor) and once
speaking normally at your intended distance.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.audio import dbfs, open_source, peak_dbfs  # noqa: E402
from voicekb.config import DEFAULT_CONFIG, Config  # noqa: E402

# Targets for speech captured at a normal talking distance.
GOOD_PEAK_MIN = -18.0  # below this, VAD starts missing quiet syllables
GOOD_PEAK_MAX = -1.0  # above this we are close to clipping
CLIPPING_PEAK = -0.2
GOOD_SNR = 20.0  # dB between the noise floor and speech


def list_devices() -> None:
    print("=== ALSA capture devices (arecord -l) ===")
    try:
        out = subprocess.run(
            ["arecord", "-l"], capture_output=True, text=True, timeout=10
        )
        print(out.stdout.strip() or out.stderr.strip() or "(none)")
    except FileNotFoundError:
        print("(arecord not found -- install alsa-utils)")
    except subprocess.TimeoutExpired:
        print("(arecord timed out)")

    print("\n=== PortAudio devices (what the code actually sees) ===")
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        print(f"(sounddevice unavailable: {exc})")
        return
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(
                f"  [{idx}] {dev['name']}  "
                f"in={dev['max_input_channels']}ch  "
                f"default_sr={int(dev['default_samplerate'])}"
            )
    print(
        "\nSet audio.device in config/default.yaml to the index above, or to a\n"
        "substring of the name (e.g. 'hw:1,0')."
    )


def record(cfg, seconds: float, out_path: Path) -> int:
    audio_cfg = cfg.audio
    n_frames = int(seconds * 1000 / audio_cfg.frame_ms)
    collected: list[np.ndarray] = []
    frame_levels: list[float] = []

    print(
        f"Device={audio_cfg.device!r} rate={audio_cfg.sample_rate} "
        f"channels={audio_cfg.channels} frame={audio_cfg.frame_ms}ms "
        f"software_gain={audio_cfg.software_gain}"
    )
    print(f"Recording {seconds:g}s -- speak now...\n")

    try:
        source = open_source(audio_cfg)
        with source:
            for i, frame in enumerate(source.frames()):
                if i >= n_frames:
                    break
                collected.append(frame)
                level = dbfs(frame)
                frame_levels.append(level)
                # One line per second, with a coarse meter.
                if i % (1000 // audio_cfg.frame_ms) == 0:
                    bars = int(max(0.0, (level + 60.0) / 60.0) * 40)
                    print(f"  t={i * audio_cfg.frame_ms / 1000:4.1f}s  "
                          f"{level:6.1f} dBFS  {'#' * bars}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nCAPTURE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "\nTry: python3 scripts/check_mic.py --list, then set audio.device.",
            file=sys.stderr,
        )
        return 1

    if not collected:
        print("No audio captured.", file=sys.stderr)
        return 1

    audio = np.concatenate(collected)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(audio_cfg.sample_rate)
        wav.writeframes(audio.tobytes())

    finite = sorted(lvl for lvl in frame_levels if np.isfinite(lvl))
    if not finite:
        print("\nSignal is digital silence -- the device produced only zeros.")
        print("The mic is enumerated but not capturing. Check `amixer` capture")
        print("settings and that the capsule is not muted.")
        return 1

    noise_floor = finite[len(finite) // 10]
    speech_level = finite[min(len(finite) - 1, len(finite) * 9 // 10)]
    snr = speech_level - noise_floor
    pk = peak_dbfs(audio)

    print(f"\n=== Results ({len(audio) / audio_cfg.sample_rate:.1f}s) ===")
    print(f"  peak         {pk:6.1f} dBFS")
    print(f"  noise floor  {noise_floor:6.1f} dBFS   (10th pct frame RMS)")
    print(f"  speech       {speech_level:6.1f} dBFS   (90th pct frame RMS)")
    print(f"  SNR          {snr:6.1f} dB")
    print(f"  saved        {out_path}")

    if source.dropped_frames or source.overflow_count:
        print(
            f"  WARNING: {source.overflow_count} overflow(s), "
            f"{source.dropped_frames} dropped frame(s)"
        )

    print("\n=== Verdict ===")
    ok = True
    if pk >= CLIPPING_PEAK:
        ok = False
        print("  CLIPPING. Lower alsa_capture_percent (or software_gain).")
    elif pk < GOOD_PEAK_MIN:
        ok = False
        deficit = GOOD_PEAK_MIN - pk
        print(f"  TOO QUIET by ~{deficit:.0f} dB.")
        print("  Fix in this order:")
        print("    1. scripts/set_gain.sh -- raise the ALSA hardware capture level")
        print(f"    2. only then raise audio.software_gain "
              f"(try {10 ** (deficit / 20):.1f}); it lifts the noise floor too")
    elif pk > GOOD_PEAK_MAX:
        print("  Peak is close to full scale -- fine, but watch for clipping when "
              "you speak up.")
    else:
        print(f"  Level OK (peak within {GOOD_PEAK_MIN:.0f}..{GOOD_PEAK_MAX:.0f} dBFS).")

    if snr < GOOD_SNR:
        ok = False
        print(f"  LOW SNR ({snr:.0f} dB, want >{GOOD_SNR:.0f} dB). If that was a")
        print("  silent recording this is expected -- rerun while speaking.")
        print("  If you were speaking: move closer, or the capsule is noisy.")
    else:
        print(f"  SNR OK ({snr:.0f} dB).")

    # This usually runs over ssh, so the file is on the Pi while the speakers are
    # not. Give the copy-back command too rather than just `aplay`.
    print("\n  Listen back to confirm it sounds like you:")
    print(f"    on the Pi:   aplay {out_path}")
    print(f"    on a Mac:    scp voicekb:{out_path} /tmp/ && afplay /tmp/{out_path.name}")
    return 0 if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="enumerate capture devices")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out", type=Path, default=Path("/tmp/voicekb_miccheck.wav"))
    args = ap.parse_args()

    if args.list:
        list_devices()
        return 0

    return record(Config.load(args.config), args.seconds, args.out)


if __name__ == "__main__":
    sys.exit(main())
