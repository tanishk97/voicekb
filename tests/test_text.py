#!/usr/bin/env python3
"""Tests for whisper-output normalization.

    python3 tests/test_text.py

The load-bearing property: whatever comes out of normalize_for_hid() must be
fully typeable on a US HID keyboard. If it is not, characters vanish silently
from the typed output and the cause is very hard to spot after the fact.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.hid_keycodes import unmappable  # noqa: E402
from voicekb.text import (  # noqa: E402
    apply_substitutions,
    collapse_whitespace,
    strip_fillers,
    fold_to_ascii,
    normalize_for_hid,
    strip_annotations,
)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


def test_typography() -> None:
    print("=== typography folding ===")
    check("curly single quote -> apostrophe", fold_to_ascii("it’s") == "it's")
    check("curly double quotes -> straight", fold_to_ascii("“q”") == '"q"')
    check("em dash -> hyphen", fold_to_ascii("a—b") == "a-b")
    check("en dash -> hyphen", fold_to_ascii("a–b") == "a-b")
    check("ellipsis -> three dots", fold_to_ascii("wait…") == "wait...")
    check("accents stripped to base letters", fold_to_ascii("café naïve") == "cafe naive")
    check("non-breaking space -> space", fold_to_ascii("a b") == "a b")
    check("plain ASCII untouched", fold_to_ascii("Hello, world! 123") == "Hello, world! 123")


def test_annotations() -> None:
    """whisper emits these for non-speech; typing them would be nonsense."""
    print("=== whisper annotations ===")
    check("[BLANK_AUDIO] removed", strip_annotations("[BLANK_AUDIO] hi").strip() == "hi")
    check("bracketed sound removed", strip_annotations("[door slams] ok").strip() == "ok")
    check("(upbeat music) removed", strip_annotations("(upbeat music) ok").strip() == "ok")
    check("ordinary parentheses kept",
          strip_annotations("call foo(bar) now") == "call foo(bar) now")


def test_whitespace() -> None:
    print("=== whitespace ===")
    check("runs of spaces collapse", collapse_whitespace("a    b") == "a b")
    check("leading/trailing stripped", collapse_whitespace("  a b  ") == "a b")
    check("newlines survive", collapse_whitespace("a\nb") == "a\nb")


def test_non_speech() -> None:
    """Utterances that are only a sound description must type nothing.

    A breath blast on the mic really did come back as "(swooshing)" during
    testing, which no keyword list would have predicted -- hence the structural
    rule rather than a vocabulary.
    """
    print("=== non-speech detection ===")
    from voicekb.text import is_non_speech

    for text in ["(swooshing)", "[BLANK_AUDIO]", "(upbeat music)",
                 "(clears throat).", "   ", "[door slams]"]:
        check(f"{text!r} is non-speech", is_non_speech(text))
        check(f"{text!r} types nothing", normalize_for_hid(text) == "")

    for text in ["Hello, hello, hello", "call foo(bar) now", "It (mostly) works"]:
        check(f"{text!r} is speech", not is_non_speech(text))
        check(f"{text!r} still types", normalize_for_hid(text) != "")


def test_end_to_end_typeable() -> None:
    print("=== everything out is typeable ===")
    samples = [
        "  it’s “quoted” — really… ",
        "café naïve résumé",
        "[BLANK_AUDIO] hello there",
        " (upbeat music) nothing said ",
        "Hey, the deploy broke again. I think it’s the auth token.",
        "Fix deploy failure caused by expired auth token.",
    ]
    for raw in samples:
        out = normalize_for_hid(raw)
        left = unmappable(out)
        check(f"typeable: {raw.strip()[:34]!r}", not left, f"-> {out!r}" if left else "")


def test_strip_fillers() -> None:
    """Deterministic, because the 1.5B model could not be trusted with it.

    Asked to remove filler, Qwen2.5-1.5B turned "The build is red and I will
    look at it after lunch" into "I will look at the build after lunch",
    dropping the only fact in the sentence. The content-overlap guard scores
    that 0.75 -- far above any usable floor -- so it cannot be caught
    structurally either.
    """
    print("=== deterministic filler removal ===")
    cases = [
        ("So, I think the odd token thing broke again. You know what, right?",
         "So, I think the odd token thing broke again."),
        ("Can you send me the report by Friday, if that makes sense?",
         "Can you send me the report by Friday?"),
        ("We should, you know, ship it today.", "We should ship it today."),
        ("uh I mean the server is down", "The server is down"),
        ("um so the deploy broke again i think it is the auth token thing you know",
         "so the deploy broke again i think it is the auth token thing"),
    ]
    for src, want in cases:
        got = strip_fillers(src)
        check(f"{src[:38]!r}", got == want, "" if got == want else f"got {got!r}")

    print("  -- must not touch real content --")
    for untouched in [
        "The build is red and I will look at it after lunch.",
        "I like the new design.",          # 'like' is ambiguous, left alone
        "Turn right at the light.",        # 'right' is ambiguous, left alone
        "It works basically everywhere.",  # 'basically' left alone
    ]:
        check(f"unchanged: {untouched[:38]!r}", strip_fillers(untouched) == untouched,
              "" if strip_fillers(untouched) == untouched else f"got {strip_fillers(untouched)!r}")

    check("empty stays empty", strip_fillers("") == "")
    check("no stray punctuation left behind", ".?" not in strip_fillers(
        "So it broke again. You know what, right?"))


def test_substitutions() -> None:
    """whisper cannot emit a word outside its vocabulary.

    "voicekb" is invented, so base.en renders it "voice he be" every time. That
    consistency is what a lookup table exploits. Asking the LLM to restore it
    was tried on both models and failed.
    """
    print("=== known mis-transcriptions ===")
    m = {"voice he be": "voicekb"}
    check("restores at the start", apply_substitutions("voice he be working", m)
          == "voicekb working")
    check("restores mid-sentence", apply_substitutions("So voice he be is running", m)
          == "So voicekb is running")
    check("case-insensitive", apply_substitutions("Voice He Be works", m) == "voicekb works")
    check("does not fire inside another word",
          apply_substitutions("the voicemail is fine", m) == "the voicemail is fine")
    check("untouched when nothing matches",
          apply_substitutions("the deploy broke", m) == "the deploy broke")
    check("empty mapping is a no-op", apply_substitutions("anything", {}) == "anything")
    check("longer phrases win over shorter ones",
          apply_substitutions("a b c", {"a b": "X", "a b c": "Y"}) == "Y")


def main() -> int:
    for fn in (test_typography, test_annotations, test_whitespace, test_non_speech,
               test_strip_fillers, test_substitutions, test_end_to_end_typeable):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
