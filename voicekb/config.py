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
    silence_ms: int = 1200
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
    # small.en, viable only with active cooling. See config/default.yaml.
    model: str = "models/ggml-small.en-q5_1.bin"
    binary: str = "vendor/whisper.cpp/build/bin/whisper-cli"
    threads: int = 4
    # Beam search costs base.en only ~0.06s over greedy on this hardware, so the
    # accuracy is effectively free. See the benchmark table in the README.
    beam_size: int = 5
    language: str = "en"
    # Known mis-transcriptions, applied verbatim after whisper. whisper cannot
    # emit a word outside its vocabulary, so invented terms come back mangled
    # the same way every time -- which is exactly what a lookup table handles.
    substitutions: tuple[tuple[str, str], ...] = (
        ("voice he be", "voicekb"),
        ("voice kb", "voicekb"),
        ("voice cabey", "voicekb"),
    )


@dataclass(frozen=True)
class LlmConfig:
    enabled: bool = True
    # AI-shaped output is the default, per the original design. Not "clean",
    # which no longer calls the model -- its filler removal is deterministic.
    profile: str = "structured"
    base_url: str = "http://127.0.0.1:8080"
    model: str = "models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    binary: str = "vendor/llama.cpp/build/bin/llama-server"
    threads: int = 4
    context_size: int = 2048
    timeout_s: float = 60.0
    # Kept for the LLM prompt, though neither model acts on it reliably.
    vocabulary: tuple[str, ...] = ("voicekb",)
    # If the server is unreachable, type the raw transcription rather than
    # dropping the utterance. Losing dictation is worse than losing formatting.
    fallback_to_raw: bool = True


@dataclass(frozen=True)
class LoggingConfig:
    # Whether the journal records the transcribed TEXT of each utterance.
    #
    # True is genuinely useful -- almost every bug in this build was diagnosed by
    # reading what whisper heard versus what got typed. But it means the journal
    # accumulates everything ever dictated into this device, which on a dictation
    # tool could be anything. Set false and the logs keep timings and lengths
    # only, which is enough to see the pipeline working without recording what
    # you said.
    log_transcripts: bool = True


@dataclass(frozen=True)
class ButtonsConfig:
    enabled: bool = True
    # "push_to_talk": hold to capture, release to transcribe. Release is a
    #   statement that you have finished, which removes the silence_ms guess
    #   entirely -- no mid-sentence splits, no room-noise false triggers, and no
    #   waiting out a silence window before anything happens.
    # "profile_cycle": each press advances through `cycle`.
    mode: str = "push_to_talk"
    # BCM numbering. GPIO17 is physical pin 11, next to a ground on pin 9.
    pin: int = 17
    bounce_ms: float = 50.0
    # Audio kept from just before the press: people often start the first
    # syllable as they press rather than after.
    pre_roll_ms: int = 200
    # Shorter than this is an accidental tap, not speech.
    min_utterance_ms: int = 200
    # Safety cap in case the button sticks or is held down by accident.
    max_hold_s: float = 120.0
    # Only used in profile_cycle mode.
    cycle: tuple[str, ...] = ("clean", "raw")

    def __post_init__(self) -> None:
        if self.mode not in ("push_to_talk", "profile_cycle"):
            raise ValueError(
                f"buttons.mode must be push_to_talk or profile_cycle, got {self.mode!r}"
            )


@dataclass(frozen=True)
class Config:
    audio: AudioConfig
    vad: VadConfig
    stt: SttConfig
    llm: LlmConfig
    buttons: ButtonsConfig
    logging: LoggingConfig

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG) -> "Config":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        stt_raw = dict(raw.get("stt", {}))
        subs = stt_raw.get("substitutions")
        if subs:
            # YAML gives a mapping; the dataclass is frozen so it needs tuples.
            stt_raw["substitutions"] = tuple(
                (k, v) for k, v in (subs.items() if isinstance(subs, dict) else subs)
            )
        btn_raw = dict(raw.get("buttons", {}))
        if btn_raw.get("cycle") is not None:
            btn_raw["cycle"] = tuple(btn_raw["cycle"])
        llm_raw = dict(raw.get("llm", {}))
        if "vocabulary" in llm_raw and llm_raw["vocabulary"] is not None:
            llm_raw["vocabulary"] = tuple(llm_raw["vocabulary"])
        return cls(
            audio=AudioConfig(**raw.get("audio", {})),
            vad=VadConfig(**raw.get("vad", {})),
            stt=SttConfig(**stt_raw),
            llm=LlmConfig(**llm_raw),
            buttons=ButtonsConfig(**btn_raw),
            logging=LoggingConfig(**raw.get("logging", {})),
        )
