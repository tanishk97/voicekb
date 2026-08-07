"""GPIO button input.

The primary mode is **push-to-talk**: hold the button to capture, release to
transcribe. This is strictly better than waiting for silence, because the
release is a *statement* that you have finished rather than an inference from
timing. It removes, in one move:

  - `vad.silence_ms`, which is pure guesswork about when a pause means "done".
    At 700ms it split single sentences at thinking pauses; at 1200ms it adds
    1.2s of latency to every utterance whether you needed it or not.
  - VAD false triggers on room noise, which cost ~2.5s of whisper each and
    produced transcriptions like "(crickets chirping)".
  - The pre-roll guesswork that exists to recover a first syllable lost to
    trigger latency.

`profile_cycle` mode is kept for a second button, but push-to-talk is the point.

gpiozero is imported lazily so this module imports fine on a machine with no
GPIO at all, including the Mac where the tests run.
"""

from __future__ import annotations

import threading
from typing import Callable

# BCM number -> physical header pin, so error messages are actionable when
# someone has miscounted the header.
PHYSICAL_PIN = {17: 11, 27: 13, 22: 15, 23: 16, 24: 18, 25: 22, 5: 29, 6: 31}


def next_in_cycle(current: str, cycle: list[str]) -> str:
    """The profile after `current`, wrapping around.

    A `current` outside the cycle -- which happens when a spoken command selects
    a profile the button does not cycle through -- returns the first entry, so a
    press always lands somewhere predictable rather than doing nothing.
    """
    if not cycle:
        return current
    try:
        return cycle[(cycle.index(current) + 1) % len(cycle)]
    except ValueError:
        return cycle[0]


class GpioButton:
    """A tactile switch with press and release callbacks.

    Both edges matter for push-to-talk, unlike profile cycling which only cares
    about the press.
    """

    def __init__(
        self,
        pin: int,
        on_press: Callable[[], None] | None = None,
        on_release: Callable[[], None] | None = None,
        bounce_ms: float = 50.0,
    ) -> None:
        self.pin = pin
        self.on_press = on_press
        self.on_release = on_release
        self.bounce_ms = bounce_ms
        self._button = None

    def start(self) -> None:
        """Claim the GPIO pin. Raises if unavailable; the caller decides severity."""
        from gpiozero import Button

        # hold_time/hold_repeat are not used: we want raw press and release
        # edges, and debounce is what keeps contact chatter from producing
        # phantom release-then-press pairs mid-hold.
        self._button = Button(
            self.pin, pull_up=True, bounce_time=self.bounce_ms / 1000.0
        )
        if self.on_press is not None:
            self._button.when_pressed = self.on_press
        if self.on_release is not None:
            self._button.when_released = self.on_release

    @property
    def is_pressed(self) -> bool:
        return bool(self._button is not None and self._button.is_pressed)

    def close(self) -> None:
        if self._button is not None:
            try:
                self._button.close()
            except Exception:  # noqa: BLE001
                pass
            self._button = None

    def where(self) -> str:
        phys = PHYSICAL_PIN.get(self.pin)
        return f"GPIO{self.pin}" + (f" (physical pin {phys})" if phys else "")


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
        self._lock = threading.Lock()
        self._current: str | None = None
        self._gpio = GpioButton(pin, on_press=self._pressed, bounce_ms=bounce_ms)

    def start(self, current_profile: str) -> None:
        self._current = current_profile
        self._gpio.start()

    def sync(self, profile: str) -> None:
        """Record a profile change made by some other route (a spoken command).

        Without this the button's idea of the current profile goes stale and the
        next press jumps somewhere surprising.
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
        self._gpio.close()

    def describe(self) -> str:
        return f"{self._gpio.where()}, cycle: {' -> '.join(self.cycle)}"
