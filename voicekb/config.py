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
class VadConfig:
    # webrtcvad aggressiveness, 0-3. Higher rejects more non-speech but starts
    # clipping quiet syllables. 2 is a reasonable middle for a clean signal.
    aggressiveness: int = 2
    # Speech must persist this long before an utterance opens (anti-click).
    start_ms: int = 150
    # Silence this long closes it. Too short splits sentences at natural pauses;
    # too long makes the whole pipeline feel laggy, since nothing runs until the
    # utterance closes.
    silence_ms: int = 700
    # Audio kept from before the trigger, so the first syllable is not lost.
    pre_roll_ms: int = 300
    # Anything shorter is a cough or a keyboard clack, not speech.
    min_utterance_ms: int = 400
    # Safety cap so a noisy room cannot buffer forever.
    max_utterance_s: float = 30.0
    # Reject utterances quieter than this (RMS dBFS) without transcribing them.
    # webrtcvad classifies on spectral shape rather than loudness, so quiet room
    # noise can open an utterance; observed as whisper returning "(crickets
    # chirping)". Those are discarded downstream anyway, but only after ~2.5s of
    # whisper each. Measured noise floor is about -53 dBFS and speech about
    # -15 dBFS, so this sits in a wide gap.
    min_level_dbfs: float = -42.0

    def __post_init__(self) -> None:
        if not 0 <= self.aggressiveness <= 3:
            raise ValueError(f"vad aggressiveness must be 0-3, got {self.aggressiveness}")


@dataclass(frozen=True)
class SttConfig:
    model: str = "models/ggml-base.en-q5_1.bin"
    binary: str = "vendor/whisper.cpp/build/bin/whisper-cli"
    threads: int = 4
    # Beam search costs base.en only ~0.06s over greedy on this hardware, so the
    # accuracy is effectively free. See the benchmark table in the README.
    beam_size: int = 5
    language: str = "en"


@dataclass(frozen=True)
class Config:
    audio: AudioConfig
    vad: VadConfig
    stt: SttConfig

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG) -> "Config":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls(
            audio=AudioConfig(**raw.get("audio", {})),
            vad=VadConfig(**raw.get("vad", {})),
            stt=SttConfig(**raw.get("stt", {})),
        )
