"""Speech to text via whisper.cpp.

Shells out to the whisper-cli binary rather than binding the library. The cost
is process startup plus reading the model, which for base.en-q5_1 (57 MB) is
small once the page cache is warm. Keeping it a subprocess means a whisper crash
or hang cannot take the capture loop down with it.

If model load ever dominates the latency budget, the upgrade path is
whisper-server (already built by setup_whisper.sh) which keeps weights resident.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import REPO_ROOT, SttConfig


@dataclass
class Transcription:
    text: str
    audio_seconds: float
    elapsed_seconds: float

    @property
    def realtime_factor(self) -> float:
        return self.elapsed_seconds / self.audio_seconds if self.audio_seconds else 0.0


class WhisperSTT:
    def __init__(self, cfg: SttConfig, repo_root: Path = REPO_ROOT) -> None:
        self.cfg = cfg
        self.binary = (repo_root / cfg.binary).resolve()
        self.model = (repo_root / cfg.model).resolve()
        if not self.binary.exists():
            raise FileNotFoundError(
                f"whisper binary not found at {self.binary}. Run scripts/setup_whisper.sh"
            )
        if not self.model.exists():
            raise FileNotFoundError(
                f"model not found at {self.model}. Run scripts/setup_whisper.sh"
            )

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcription:
        """Transcribe 16 kHz mono int16 audio."""
        audio_seconds = len(audio) / sample_rate
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            with wave.open(tmp.name, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(audio.tobytes())

            cmd = [
                str(self.binary),
                "-m", str(self.model),
                "-f", tmp.name,
                "-t", str(self.cfg.threads),
                "-l", self.cfg.language,
                "-bs", str(self.cfg.beam_size),
                "-nt",            # no timestamps; we want bare text
                "--no-prints",    # keep the model's chatter off stdout
            ]
            start = time.perf_counter()
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            elapsed = time.perf_counter() - start

        if proc.returncode != 0:
            raise RuntimeError(
                f"whisper-cli failed ({proc.returncode}): {proc.stderr.strip()[:400]}"
            )
        return Transcription(
            text=" ".join(proc.stdout.split()),
            audio_seconds=audio_seconds,
            elapsed_seconds=elapsed,
        )
