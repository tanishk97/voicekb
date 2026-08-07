#!/usr/bin/env python3
"""Tests for GPIO button profile cycling. No GPIO hardware needed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from voicekb.buttons import PHYSICAL_PIN, ProfileButton, next_in_cycle  # noqa: E402
from voicekb.vad import PushToTalkSegmenter  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


def test_cycle() -> None:
    print("=== profile cycling ===")
    two = ["clean", "raw"]
    check("clean -> raw", next_in_cycle("clean", two) == "raw")
    check("raw wraps to clean", next_in_cycle("raw", two) == "clean")

    five = ["clean", "slack", "commit", "email", "raw"]
    check("steps forward", next_in_cycle("slack", five) == "commit")
    check("wraps at the end", next_in_cycle("raw", five) == "clean")

    # A spoken command can select a profile the button does not cycle through.
    # A press must still land somewhere predictable rather than doing nothing.
    check("profile outside the cycle lands on the first entry",
          next_in_cycle("commit", two) == "clean")
    check("empty cycle is a no-op", next_in_cycle("clean", []) == "clean")
    check("single-entry cycle stays put", next_in_cycle("clean", ["clean"]) == "clean")


def test_button_without_gpio() -> None:
    """Constructing must not need hardware; only start() touches GPIO."""
    print("=== construction is hardware-free ===")
    seen: list[tuple[str, str]] = []
    b = ProfileButton(17, ["clean", "raw"], lambda o, n: seen.append((o, n)))
    check("constructs with no GPIO present", b.pin == 17)
    check("describe names the physical pin", "physical pin 11" in b.describe(),
          b.describe())
    check("close() is safe before start()", b.close() is None)

    b.sync("raw")
    b._pressed()
    check("sync then press advances from the synced value", seen == [("raw", "clean")],
          f"{seen}")


def test_pin_map() -> None:
    print("=== pin mapping ===")
    check("GPIO17 is physical 11", PHYSICAL_PIN[17] == 11)
    check("GPIO27 is physical 13", PHYSICAL_PIN[27] == 13)


def test_push_to_talk() -> None:
    """Release ends the utterance, full stop -- no timing inference."""
    print("=== push-to-talk segmentation ===")
    sr, fms = 16000, 30
    seg = PushToTalkSegmenter(sr, fms, pre_roll_ms=200, min_utterance_ms=200)
    frame = lambda v: np.full(480, v, np.int16)  # noqa: E731

    got = [seg.push(frame(1)) for _ in range(10)]
    check("nothing captured while released", all(g is None for g in got))

    seg.set_held(True)
    got = [seg.push(frame(2)) for _ in range(30)]
    check("nothing emitted while still held", all(g is None for g in got))

    seg.set_held(False)
    out = seg.push(frame(1))
    check("release emits the utterance", out is not None)
    if out is not None:
        check("pre-roll is included", int((out == 1).sum()) > 0)
        check("held audio is included", int((out == 2).sum()) == 30 * 480)

    print("  -- silence during a hold must NOT end it --")
    seg2 = PushToTalkSegmenter(sr, fms, pre_roll_ms=0, min_utterance_ms=200)
    seg2.set_held(True)
    mid = [seg2.push(frame(0)) for _ in range(200)]  # 6s of pure silence
    check("6s of silence mid-hold emits nothing", all(g is None for g in mid))
    seg2.set_held(False)
    check("still emits on release", seg2.push(frame(0)) is not None)

    print("  -- guards --")
    seg3 = PushToTalkSegmenter(sr, fms, pre_roll_ms=0, min_utterance_ms=200)
    seg3.set_held(True); seg3.push(frame(3)); seg3.set_held(False)
    check("an accidental tap is discarded", seg3.push(frame(0)) is None)
    check("  and counted", seg3.rejected_short == 1)

    # A stuck button chunks rather than buffering without bound: each time the
    # cap is hit the audio is emitted and a fresh segment starts. 40 frames
    # against a 10-frame cap therefore yields 4 chunks, not 1.
    seg4 = PushToTalkSegmenter(sr, fms, pre_roll_ms=0, min_utterance_ms=30,
                               max_utterance_s=0.3)
    seg4.set_held(True)
    outs = [o for o in (seg4.push(frame(4)) for _ in range(40)) if o is not None]
    check("a stuck button is capped, not unbounded", len(outs) == 4, f"{len(outs)} chunks")
    check("  each chunk is bounded", all(o.size == 10 * 480 for o in outs))
    check("  truncations counted", seg4.truncated == 4, f"{seg4.truncated}")


def main() -> int:
    for fn in (test_cycle, test_button_without_gpio, test_pin_map, test_push_to_talk):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
