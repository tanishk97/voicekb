#!/usr/bin/env python3
"""Tests for HID keycode mapping. No Bluetooth, no hardware.

    python3 tests/test_hid.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.hid_keycodes import (  # noqa: E402
    MOD_LGUI,
    MOD_LSHIFT,
    MOD_NONE,
    RELEASE_REPORT,
    build_report,
    key_for_char,
    key_for_name,
    unmappable,
)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


def test_letters() -> None:
    print("=== letters and digits ===")
    check("'a' is usage 0x04 unshifted", key_for_char("a") == (0x04, MOD_NONE))
    check("'z' is usage 0x1D", key_for_char("z") == (0x1D, MOD_NONE))
    check("'A' is 'a' plus shift", key_for_char("A") == (0x04, MOD_LSHIFT))
    check("'1' is usage 0x1E", key_for_char("1") == (0x1E, MOD_NONE))
    # '0' breaks the run: it sits after 9, not before 1.
    check("'0' is usage 0x27, not 0x1D", key_for_char("0") == (0x27, MOD_NONE))
    check("'9' is usage 0x26", key_for_char("9") == (0x26, MOD_NONE))


def test_symbols() -> None:
    print("=== symbols ===")
    check("'!' is '1' plus shift", key_for_char("!") == (0x1E, MOD_LSHIFT))
    check("'?' is '/' plus shift", key_for_char("?") == (0x38, MOD_LSHIFT))
    check("':' is ';' plus shift", key_for_char(":") == (0x33, MOD_LSHIFT))
    check('\'"\' is "\'" plus shift', key_for_char('"') == (0x34, MOD_LSHIFT))
    check("space is 0x2C", key_for_char(" ") == (0x2C, MOD_NONE))
    check("newline maps to Enter", key_for_char("\n") == (0x28, MOD_NONE))


def test_named() -> None:
    print("=== named keys (spoken commands) ===")
    check("enter", key_for_name("enter") == (0x28, MOD_NONE))
    check("'return' aliases enter", key_for_name("return") == (0x28, MOD_NONE))
    check("up arrow", key_for_name("up") == (0x52, MOD_NONE))
    check("down arrow", key_for_name("down") == (0x51, MOD_NONE))
    check("case and spacing tolerant", key_for_name("Page Up") == (0x4B, MOD_NONE))
    check("modifiers combine", key_for_name("left", MOD_LGUI) == (0x50, MOD_LGUI))
    check("unknown name returns None", key_for_name("flurb") is None)


def test_reports() -> None:
    print("=== report framing ===")
    r = build_report(key_for_char("a"))
    check("report is 8 bytes", len(r) == 8, f"got {len(r)}")
    check("byte 0 is modifiers", r[0] == MOD_NONE)
    check("byte 1 is reserved zero", r[1] == 0x00)
    check("byte 2 is the usage code", r[2] == 0x04)
    check("unused key slots are zero", r[3:] == bytes(5))

    shifted = build_report(key_for_char("A"))
    check("shift appears in byte 0", shifted[0] == MOD_LSHIFT and shifted[2] == 0x04)

    check("None builds the all-up report", build_report(None) == bytes(8))
    check("RELEASE_REPORT is all zeros", RELEASE_REPORT == bytes(8))


def test_unmappable() -> None:
    """whisper emits typography a US keyboard has no codes for."""
    print("=== unmappable characters ===")
    check("plain ASCII is fully mappable", unmappable("Hello, world! (test 123)") == [])
    smart = unmappable("it’s “quoted” — really")
    check("catches smart quotes and em dash",
          set(smart) == {"’", "“", "”", "—"}, f"got {smart}")
    check("catches accented characters", unmappable("café") == ["é"])
    check("every ASCII printable maps",
          unmappable("".join(chr(c) for c in range(32, 127))) == [])


def main() -> int:
    for fn in (test_letters, test_symbols, test_named, test_reports, test_unmappable):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
