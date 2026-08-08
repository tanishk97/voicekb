#!/usr/bin/env python3
"""Tests for spoken command recognition. No hardware.

    python3 tests/test_commands.py

The most important tests here are the NEGATIVE ones. Failing to recognise a
command costs one repeat; misrecognising dictation as a command destroys the
sentence you actually said.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.commands import (  # noqa: E402
    MAX_REPEAT,
    KeyCommand,
    ProfileCommand,
    parse,
)
from voicekb.hid_keycodes import MOD_LGUI, MOD_LSHIFT, MOD_NONE  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


def test_profiles() -> None:
    print("=== profile switching ===")
    for phrase, want in [
        ("commit mode", "commit"),
        ("switch to slack mode", "slack"),
        ("change to raw mode", "raw"),
        ("go to email mode", "email"),
        ("use clean mode", "clean"),
        ("Commit mode.", "commit"),          # whisper capitalises and punctuates
        ("switch to commit profile", "commit"),
    ]:
        got = parse(phrase)
        check(f"{phrase!r}", got == ProfileCommand(want), f"got {got}")


def test_keys() -> None:
    print("=== key commands ===")
    check("'press enter'", parse("press enter") == KeyCommand("enter", MOD_NONE, 1))
    check("'hit escape'", parse("hit escape") == KeyCommand("escape", MOD_NONE, 1))
    check("'go up twice'", parse("go up twice") == KeyCommand("up", MOD_NONE, 2))
    check("'press down 3 times'", parse("press down 3 times") == KeyCommand("down", MOD_NONE, 3))
    check("'press tab three times'",
          parse("press tab three times") == KeyCommand("tab", MOD_NONE, 3))
    check("'press command left'",
          parse("press command left") == KeyCommand("left", MOD_LGUI, 1))
    check("'press shift tab'", parse("press shift tab") == KeyCommand("tab", MOD_LSHIFT, 1))
    check("'press the enter key'", parse("press the enter key") == KeyCommand("enter", MOD_NONE, 1))
    check("repeat is capped", (parse("press down 999 times") or KeyCommand("x")).count
          == MAX_REPEAT)
    check("'page up' is one key, not modifier+key",
          parse("press page up") == KeyCommand("pageup", MOD_NONE, 1))
    # A bare direction as the whole utterance is a command: nobody dictates
    # "go up" as an entire sentence, and the cost of being wrong is two words.
    check("'go up' is a command", parse("go up") == KeyCommand("up", MOD_NONE, 1))
    # Single characters, so macOS's Keyboard Setup Assistant can be answered:
    # it asks for the key right of left Shift, which is "z" on a US layout.
    check("'press z' sends the letter", parse("press z") == KeyCommand("z", MOD_NONE, 1))
    check("'press 5' sends the digit", parse("press 5") == KeyCommand("5", MOD_NONE, 1))
    check("a letter still needs the verb", parse("z") is None)


def test_not_commands() -> None:
    """Ordinary dictation must never be swallowed as a command."""
    print("=== must NOT be treated as commands ===")
    for phrase in [
        "press enter your name in the form",     # command-shaped prefix, real sentence
        "I had to press enter twice before it worked",
        "we should go up to the roof",
        "the commit mode of this repo is weird",
        "switch to slack mode is what I told him",
        "press the big red button",              # unknown key name
        "email mode of transport",
        "raw",                                   # bare profile name
        "commit",
        "press",
        "",
        "So, I think there are two issues in the system.",
        "Fix deploy issue with auth token.",
    ]:
        got = parse(phrase)
        check(f"{phrase[:44]!r} is dictation", got is None, f"got {got}")


def main() -> int:
    for fn in (test_profiles, test_keys, test_not_commands):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
