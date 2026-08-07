"""Voice activity detection: turn a continuous frame stream into utterances.

Uses webrtcvad — a small C library that costs almost nothing on the CPU, which
matters on a Pi with no active cooler that also has to run whisper. Silero is
more accurate but pulls in PyTorch, adds seconds to startup, and competes for
the same cores.

The state machine is the standard hysteresis approach, and the hysteresis is the
point: a bare per-frame speech/silence flag chatters badly at the boundaries,
splitting one sentence into several utterances. Requiring a *run* of speech
frames to open and a longer run of silence to close gives stable segments.

Pre-roll matters too. By the time enough speech frames have accumulated to
trigger, the first syllable is already past, so the buffer keeps a short history
and prepends it. Without that, utterances reliably lose their first word.
"""

from __future__ import annotations

import threading
from collections import deque
from enum import Enum

import numpy as np

from .audio import dbfs
from .config import VadConfig


class State(Enum):
    IDLE = "idle"
    SPEAKING = "speaking"


class VadSegmenter:
    """Feed frames in with push(); complete utterances come back out."""

    def __init__(self, cfg: VadConfig, sample_rate: int, frame_ms: int) -> None:
        import webrtcvad

        if frame_ms not in (10, 20, 30):
            raise ValueError(f"webrtcvad requires 10/20/30 ms frames, got {frame_ms}")
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError(f"webrtcvad cannot run at {sample_rate} Hz")

        self.cfg = cfg
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self._vad = webrtcvad.Vad(cfg.aggressiveness)

        self._pre_roll = deque(maxlen=max(1, cfg.pre_roll_ms // frame_ms))
        self._start_window = deque(maxlen=max(1, cfg.start_ms // frame_ms))
        self._silence_window = deque(maxlen=max(1, cfg.silence_ms // frame_ms))

        self.state = State.IDLE
        self.rejected_short = 0
        self.rejected_quiet = 0
        self._utterance: list[np.ndarray] = []
        self._frames_in_utterance = 0
        self._max_frames = int(cfg.max_utterance_s * 1000 / frame_ms)
        self._min_frames = max(1, cfg.min_utterance_ms // frame_ms)

    def is_speech(self, frame: np.ndarray) -> bool:
        return self._vad.is_speech(frame.tobytes(), self.sample_rate)

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        """Add one frame. Returns a finished utterance, or None."""
        speech = self.is_speech(frame)

        if self.state is State.IDLE:
            self._pre_roll.append(frame)
            self._start_window.append(speech)
            # Open only on a solid majority, so a single click cannot trigger.
            if (self._start_window.maxlen == len(self._start_window)
                    and sum(self._start_window) >= self._start_window.maxlen * 0.8):
                self.state = State.SPEAKING
                self._utterance = list(self._pre_roll)
                self._frames_in_utterance = len(self._utterance)
                self._pre_roll.clear()
                self._start_window.clear()
                self._silence_window.clear()
            return None

        # SPEAKING
        self._utterance.append(frame)
        self._frames_in_utterance += 1
        self._silence_window.append(not speech)

        hit_cap = self._frames_in_utterance >= self._max_frames
        went_quiet = (
            self._silence_window.maxlen == len(self._silence_window)
            and all(self._silence_window)
        )
        if went_quiet or hit_cap:
            return self._finish()
        return None

    def _finish(self) -> np.ndarray | None:
        frames, self._utterance = self._utterance, []
        self.state = State.IDLE
        self._frames_in_utterance = 0
        self._silence_window.clear()
        self._start_window.clear()
        self._pre_roll.clear()
        if len(frames) < self._min_frames:
            self.rejected_short += 1
            return None  # a cough, a door, a keyboard clack
        audio = np.concatenate(frames)

        # Energy gate. webrtcvad classifies on spectral shape, not loudness, so
        # quiet room noise can look enough like speech to open an utterance --
        # observed in practice as whisper returning "(crickets chirping)" and
        # "[BLANK_AUDIO]". Those get discarded downstream anyway, but only after
        # paying ~2.5s of whisper for each. Rejecting on level here is free.
        #
        # The gap this sits in is wide: measured noise floor is about -53 dBFS
        # and speech about -15 dBFS.
        level = dbfs(audio)
        if level < self.cfg.min_level_dbfs:
            self.rejected_quiet += 1
            return None
        return audio

    def flush(self) -> np.ndarray | None:
        """End any in-progress utterance, e.g. on shutdown."""
        if self.state is State.SPEAKING:
            return self._finish()
        return None


class PushToTalkSegmenter:
    """Segment by button state instead of by silence.

    Same interface as VadSegmenter -- push() frames in, get an utterance back --
    so the pipeline does not care which one it is holding.

    The whole point is that release is a *statement* that you have finished,
    not an inference from timing. That removes the silence_ms guess, which was
    never right: 700ms split single sentences at ordinary thinking pauses, and
    1200ms fixed that by adding 1.2s of latency to every single utterance.

    It also removes VAD false triggers wholesale. Nothing is captured unless you
    are holding the button, so room noise cannot open an utterance and cost 2.5s
    of whisper to discover it was "(crickets chirping)".
    """

    def __init__(
        self,
        sample_rate: int,
        frame_ms: int,
        pre_roll_ms: int = 200,
        min_utterance_ms: int = 200,
        max_utterance_s: float = 120.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        # A small pre-roll still helps: people commonly start the first syllable
        # as they press rather than after. Much shorter than the VAD's, which
        # had to cover trigger latency as well.
        self._pre_roll = deque(maxlen=max(1, pre_roll_ms // frame_ms))
        self._min_frames = max(1, min_utterance_ms // frame_ms)
        self._max_frames = int(max_utterance_s * 1000 / frame_ms)

        self._held = False
        self._utterance: list[np.ndarray] = []
        self._lock = threading.Lock()
        self.rejected_short = 0
        self.truncated = 0

    @property
    def state(self) -> State:
        return State.SPEAKING if self._held else State.IDLE

    def set_held(self, held: bool) -> None:
        """Called from the button's callback thread."""
        with self._lock:
            self._held = held

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        with self._lock:
            held = self._held

        if not held:
            if self._utterance:
                return self._finish()
            # Keep a rolling pre-roll so audio just before the press survives.
            self._pre_roll.append(frame)
            return None

        if not self._utterance:
            # Opening: seed with the pre-roll captured before the press landed.
            self._utterance = list(self._pre_roll)
            self._pre_roll.clear()
        self._utterance.append(frame)

        # Safety cap, in case the button sticks or is taped down.
        if len(self._utterance) >= self._max_frames:
            self.truncated += 1
            return self._finish()
        return None

    def _finish(self) -> np.ndarray | None:
        frames, self._utterance = self._utterance, []
        self._pre_roll.clear()
        if len(frames) < self._min_frames:
            self.rejected_short += 1
            return None  # an accidental tap, not speech
        return np.concatenate(frames)

    def flush(self) -> np.ndarray | None:
        """End any in-progress utterance, e.g. on shutdown."""
        if self._utterance:
            return self._finish()
        return None
