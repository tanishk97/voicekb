"""GPIO button input: switch profiles without speaking or SSHing in.

Wiring is one tactile switch between a GPIO pin and ground, with the internal
pull-up enabled. No resistor, no other components.

The default cycle is deliberately SHORT. There is no display on this device, so
after a press you cannot see which profile you landed on -- and blindly stepping
through five modes is worse than useless. The original design called for
AI-cleaned as the default with raw as "the toggle-to fallback", which is exactly
a two-position switch, so that is what the default cycle is. Lengthen
`buttons.cycle` in config if you add an indicator LED.

gpiozero is imported lazily so this module can be imported, and the rest of the
pipeline can run, on a machine with no GPIO at all -- including the Mac, where
the tests run.
"""

from __future__ import annotations

import threading
from typing import Callable

# BCM number -> physical header pin, for error messages that are actually
# actionable when someone has miscounted the header.
PHYSICAL_PIN = {17: 11, 27: 13, 22: 15, 23: 16, 24: 18, 25: 22, 5: 29, 6: 31}


def next_in_cycle(current: str, cycle: list[str]) -> str:
    """The profile after `current`, wrapping around.

    A `current` value outside the cycle -- which happens when a spoken command
    selects a profile the button does not cycle through -- returns the first
    entry, so a press always lands somewhere predictable rather than doing
    nothing.
    """
    if not cycle:
        return current
    try:
        return cycle[(cycle.index(current) + 1) % len(cycle)]
    except ValueError:
        return cycle[0]


class ProfileButton:
    """A tactile switch that advances the active profile on each press."""

    def __init__(
        self,
        pin: int,
        cycle: list[str],
        on_change: Callable[[str, str], None],
        bounce_ms: float = 50.0,
    ) -> None:
        self.pin = pin
        self.cycle = list(cycle)
        self.on_change = on_change
        self.bounce_ms = bounce_ms
        self._button = None
        self._lock = threading.Lock()
        self._current: str | None = None

    def start(self, current_profile: str) -> None:
        """Claim the GPIO pin. Raises if unavailable; callers decide if fatal."""
        from gpiozero import Button

        self._current = current_profile
        # bounce_time is what stops one physical press registering as several;
        # tactile switches chatter for a few milliseconds on contact.
        self._button = Button(
            self.pin, pull_up=True, bounce_time=self.bounce_ms / 1000.0
        )
        self._button.when_pressed = self._pressed

    def sync(self, profile: str) -> None:
        """Tell the button the profile changed by some other route.

        Without this, a spoken "commit mode" would leave the button's idea of
        the current profile stale, and the next press would jump somewhere
        surprising.
        """
        with self._lock:
            self._current = profile

    def _pressed(self) -> None:
        with self._lock:
            old = self._current or (self.cycle[0] if self.cycle else "clean")
            new = next_in_cycle(old, self.cycle)
            self._current = new
        if new != old:
            self.on_change(old, new)

    def close(self) -> None:
        if self._button is not None:
            try:
                self._button.close()
            except Exception:  # noqa: BLE001
                pass
            self._button = None

    def describe(self) -> str:
        phys = PHYSICAL_PIN.get(self.pin)
        where = f"GPIO{self.pin}" + (f" (physical pin {phys})" if phys else "")
        return f"{where}, cycle: {' -> '.join(self.cycle)}"
