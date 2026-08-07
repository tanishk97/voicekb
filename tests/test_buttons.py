#!/usr/bin/env python3
"""Tests for GPIO button profile cycling. No GPIO hardware needed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voicekb.buttons import PHYSICAL_PIN, ProfileButton, next_in_cycle  # noqa: E402

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


def main() -> int:
    for fn in (test_cycle, test_button_without_gpio, test_pin_map):
        fn()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
