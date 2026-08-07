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
    collapse_whitespace,
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


def main() -> int:
    for fn in (test_typography, test_annotations, test_whitespace, test_end_to_end_typeable):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
