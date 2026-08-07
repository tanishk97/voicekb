#!/usr/bin/env python3
"""Stage 6 verification: is the button wired correctly and does it read cleanly?

    ./.venv/bin/python scripts/test_button.py

Wiring: one switch leg to GPIO17 (physical pin 11), the other to GND (physical
pin 9). No resistor -- the internal pull-up is enabled here, so the pin idles
HIGH and a press pulls it LOW.

Use DIAGONALLY OPPOSITE legs. A 4-pin tactile switch is really a 2-pin switch:
the two legs on each side are permanently bridged, and pressing connects one
side to the other. Two legs from the same side read as permanently pressed,
which this script detects and says so, because the symptom otherwise looks like
a software bug.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_PIN = 17
PHYSICAL = {17: 11, 27: 13, 22: 15, 23: 16, 24: 18, 25: 22}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pin", type=int, default=DEFAULT_PIN, help="BCM GPIO number")
    ap.add_argument("--seconds", type=float, default=30.0)
    # Tactile switches bounce for a few ms; without debounce one press reads as
    # several. 50ms is comfortably longer than the bounce and far shorter than
    # any human double-press.
    ap.add_argument("--bounce-ms", type=float, default=50.0)
    args = ap.parse_args()

    try:
        from gpiozero import Button
    except Exception as exc:  # noqa: BLE001
        print(f"gpiozero unavailable: {exc}", file=sys.stderr)
        print("  sudo apt install -y python3-gpiozero python3-lgpio", file=sys.stderr)
        return 1

    phys = PHYSICAL.get(args.pin)
    where = f"GPIO{args.pin}" + (f" (physical pin {phys})" if phys else "")
    print(f"watching {where}, pull-up enabled, debounce {args.bounce_ms:.0f}ms")

    try:
        button = Button(args.pin, pull_up=True, bounce_time=args.bounce_ms / 1000.0)
    except Exception as exc:  # noqa: BLE001
        print(f"could not claim {where}: {exc}", file=sys.stderr)
        print("  is another process using it? try: gpioinfo | grep -w 17", file=sys.stderr)
        return 1

    # Read the resting state before asking for any press. This is the check that
    # catches the most likely wiring mistake.
    time.sleep(0.2)
    if button.is_pressed:
        print()
        print("  PROBLEM: the button reads as PRESSED with nothing touching it.")
        print("  Almost certainly both wires are on legs from the SAME side of the")
        print("  switch, which are bridged internally. Move one wire to the leg")
        print("  DIAGONALLY opposite. (A wire shorting GPIO to GND does this too.)")
        print()
    else:
        print("  idle state OK (not pressed)")

    print(f"\npress the button -- listening for {args.seconds:.0f}s, Ctrl-C to stop\n")
    presses = 0
    last = 0.0

    def on_press() -> None:
        nonlocal presses, last
        presses += 1
        now = time.monotonic()
        gap = f"  (+{now - last:.2f}s)" if last else ""
        last = now
        print(f"  [{time.strftime('%H:%M:%S')}] press #{presses}{gap}", flush=True)

    def on_release() -> None:
        print(f"  [{time.strftime('%H:%M:%S')}]   release", flush=True)

    button.when_pressed = on_press
    button.when_released = on_release

    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass

    print(f"\n{presses} press(es) detected")
    if presses == 0:
        print("  Nothing registered. Check in this order:")
        print("   1. Both jumpers actually gripping the legs -- F-F sockets are")
        print("      loose on flat switch legs and fall off easily.")
        print(f"   2. Wires on GPIO{args.pin} (physical {phys}) and a GND pin.")
        print("   3. Legs are diagonally opposite, not the same side.")
    elif presses > 1:
        print("  If you pressed once and see several, raise --bounce-ms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
