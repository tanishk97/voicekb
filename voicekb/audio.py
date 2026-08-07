"""Audio capture.

Everything downstream of this module -- VAD, whisper.cpp -- consumes a stream of
16 kHz mono int16 frames and knows nothing about the microphone. Swapping the
USB capsule for a beamforming array is a `config/default.yaml` edit: raise
`channels`, point `channel_select` at the array's beamformed output, and update
`device`. Nothing below this line changes.
"""

from __future__ import annotations

import math
import queue
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Iterator

import numpy as np

from .config import AudioConfig

INT16_FULL_SCALE = 32768.0


def dbfs(frame: np.ndarray) -> float:
    """RMS level of an int16 frame in dBFS. Silence returns -inf."""
    if frame.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
    if rms <= 0:
        return -math.inf
    return 20.0 * math.log10(rms / INT16_FULL_SCALE)


def peak_dbfs(frame: np.ndarray) -> float:
    if frame.size == 0:
        return -math.inf
    peak = int(np.max(np.abs(frame.astype(np.int32))))
    if peak <= 0:
        return -math.inf
    return 20.0 * math.log10(peak / INT16_FULL_SCALE)


def to_mono(block: np.ndarray, channel_select: int | None) -> np.ndarray:
    """Collapse an (n_samples, n_channels) int16 block to (n_samples,) int16."""
    if block.ndim == 1:
        return block
    if block.shape[1] == 1:
        return block[:, 0]
    if channel_select is not None:
        return block[:, channel_select]
    # Average in int32 to avoid wrapping before the divide.
    return (block.astype(np.int32).mean(axis=1)).astype(np.int16)


def apply_gain(frame: np.ndarray, gain: float) -> np.ndarray:
    """Scale an int16 frame, clipping rather than wrapping on overflow."""
    if gain == 1.0:
        return frame
    scaled = frame.astype(np.float32) * gain
    return np.clip(scaled, -INT16_FULL_SCALE, INT16_FULL_SCALE - 1).astype(np.int16)


class AudioSource(ABC):
    """A source of 16 kHz mono int16 frames of `cfg.frame_samples` length."""

    def __init__(self, cfg: AudioConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    def frames(self) -> Iterator[np.ndarray]: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    def __enter__(self) -> "AudioSource":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


def _resolve_device(device: str) -> str | int:
    """PortAudio takes an index or a name substring. Allow either in config."""
    text = str(device).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return text


class SoundDeviceSource(AudioSource):
    """PortAudio capture via python-sounddevice.

    Capture runs on PortAudio's own thread and hands frames over a bounded queue,
    so a slow consumer (a whisper.cpp call blocking the main thread) drops old
    audio instead of growing memory without bound.
    """

    def __init__(self, cfg: AudioConfig, queue_frames: int = 100) -> None:
        super().__init__(cfg)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=queue_frames)
        self._stream = None
        self.overflow_count = 0
        self.dropped_frames = 0

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            self.overflow_count += 1
        try:
            self._queue.put_nowait(indata.copy())
        except queue.Full:
            self.dropped_frames += 1

    def start(self) -> None:
        import sounddevice as sd  # imported late so `--list` works without a device

        cfg = self.cfg
        self._stream = sd.InputStream(
            device=_resolve_device(cfg.device),
            samplerate=cfg.sample_rate,
            channels=cfg.channels,
            dtype="int16",
            blocksize=cfg.frame_samples,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def frames(self) -> Iterator[np.ndarray]:
        cfg = self.cfg
        while True:
            block = self._queue.get()
            yield apply_gain(to_mono(block, cfg.channel_select), cfg.software_gain)


def open_source(cfg: AudioConfig) -> AudioSource:
    """Factory. Add backends here; callers stay backend-agnostic."""
    return SoundDeviceSource(cfg)
