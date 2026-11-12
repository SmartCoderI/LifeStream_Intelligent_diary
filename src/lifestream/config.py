"""Settings, read from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass
class Settings:
    # Speech recognition (WhisperX). Local Mac: medium.en/int8 on CPU.
    # Cloud GPU: large-v3/float16 on cuda.
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "medium.en"))
    language: str = field(default_factory=lambda: _env("LANGUAGE", "en"))
    device: str = field(default_factory=lambda: _env("DEVICE", "cpu"))
    compute_type: str = field(default_factory=lambda: _env("COMPUTE_TYPE", "int8"))
    asr_batch_size: int = field(default_factory=lambda: int(_env("ASR_BATCH_SIZE", "8")))

    # Diarization (pyannote, acoustic only).
    hf_token: str | None = field(default_factory=lambda: _env("HF_TOKEN"))
    diarization_model: str = field(
        default_factory=lambda: _env("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")
    )
    diarization_device: str = field(default_factory=lambda: _env("DIARIZATION_DEVICE", "cpu"))

    # Scene detection / keyframes.
    scene_threshold: float = field(default_factory=lambda: float(_env("SCENE_THRESHOLD", "27.0")))
    min_scene_len_s: float = field(default_factory=lambda: float(_env("MIN_SCENE_LEN_S", "2.0")))
    max_keyframes: int = field(default_factory=lambda: int(_env("MAX_KEYFRAMES", "20")))
    keyframe_width: int = field(default_factory=lambda: int(_env("KEYFRAME_WIDTH", "768")))

    # LLM (classification + summaries).
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-opus-5-5"))
    llm_effort: str = field(default_factory=lambda: _env("LLM_EFFORT", "medium"))
    classify_window_s: float = field(default_factory=lambda: float(_env("CLASSIFY_WINDOW_S", "300")))


def get_settings() -> Settings:
    return Settings()
