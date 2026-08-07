"""Configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "default.yaml"


@dataclass(frozen=True)
class AudioConfig:
    device: str = "default"
    sample_rate: int = 16000
    frame_ms: int = 30
    channels: int = 1
    channel_select: int | None = None
    alsa_capture_percent: int = 62
    software_gain: float = 1.0
    highpass_hz: float = 80.0  # 0 disables

    @property
    def frame_samples(self) -> int:
        """Samples per frame, per channel."""
        return self.sample_rate * self.frame_ms // 1000

    def __post_init__(self) -> None:
        if self.frame_ms not in (10, 20, 30):
            raise ValueError(f"frame_ms must be 10, 20, or 30 for webrtcvad; got {self.frame_ms}")
        if self.channel_select is not None and not 0 <= self.channel_select < self.channels:
            raise ValueError(
                f"channel_select {self.channel_select} out of range for {self.channels} channel(s)"
            )


@dataclass(frozen=True)
class Config:
    audio: AudioConfig

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG) -> "Config":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls(audio=AudioConfig(**raw.get("audio", {})))
