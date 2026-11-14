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
    # On a GPU server, nvidia/Nemotron-3-Diarization is a candidate replacement; see the note at the
    # top of processing/diarize.py.
    hf_token: str | None = field(default_factory=lambda: _env("HF_TOKEN"))
    diarization_model: str = field(
        default_factory=lambda: _env("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")
    )
    diarization_device: str = field(default_factory=lambda: _env("DIARIZATION_DEVICE", "cpu"))
    # pyannote's analysis window (10 s) moves by this share of its length: 0.1 = pyannote's default
    # (every second analysed ~10 times); larger = fewer windows and voice embeddings, faster.
    diarization_step: float = field(default_factory=lambda: float(_env("DIARIZATION_STEP", "0.1")))
    # Diarization alongside transcription (they only meet at the merge). auto = when both run on
    # a GPU; on a CPU they would just compete for the same cores. true / false to force it.
    parallel_audio_mode: str = field(default_factory=lambda: _env("PARALLEL_AUDIO", "auto"))

    # Scene detection / keyframes.
    scene_threshold: float = field(default_factory=lambda: float(_env("SCENE_THRESHOLD", "27.0")))
    min_scene_len_s: float = field(default_factory=lambda: float(_env("MIN_SCENE_LEN_S", "2.0")))
    # Frames per second compared for cuts (0 = every frame). Decoding still reads every frame, but
    # skipped ones aren't converted or compared: ~3x faster with the same cuts on our videos.
    scene_sample_fps: float = field(default_factory=lambda: float(_env("SCENE_SAMPLE_FPS", "5")))
    max_keyframes: int = field(default_factory=lambda: int(_env("MAX_KEYFRAMES", "20")))
    keyframe_width: int = field(default_factory=lambda: int(_env("KEYFRAME_WIDTH", "768")))

    # LLM (classification, summaries, answers). LLM_PROVIDER picks which one runs:
    #   claude -> Claude via the Anthropic API (LLM_MODEL, LLM_EFFORT, ANTHROPIC_API_KEY)
    #   qwen   -> currently commented out in intelligence/llm.py make_llm()
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "claude"))

    # Claude (the active LLM).
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-opus-5-5"))
    llm_effort: str = field(default_factory=lambda: _env("LLM_EFFORT", "medium"))

    # Qwen (inactive: see make_llm). Needs a vision model: classify and summarize send keyframes.
    # Default: Alibaba Cloud Model Studio (international). Local Ollama: http://localhost:11434/v1
    qwen_model: str = field(default_factory=lambda: _env("QWEN_MODEL", "qwen3-vl-plus"))
    qwen_base_url: str = field(default_factory=lambda: _env(
        "QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"))
    qwen_api_key: str | None = field(default_factory=lambda: _env("QWEN_API_KEY", _env("DASHSCOPE_API_KEY")))
    qwen_max_tokens: int = field(default_factory=lambda: int(_env("QWEN_MAX_TOKENS", "8192")))
    qwen_json_mode: bool = field(default_factory=lambda: _env("QWEN_JSON_MODE", "true").lower() != "false")
    classify_window_s: float = field(default_factory=lambda: float(_env("CLASSIFY_WINDOW_S", "300")))

    # Memory (Stage 2): Postgres + pgvector, local embeddings, speaker names.
    database_url: str = field(default_factory=lambda: _env(
        "DATABASE_URL", "postgresql://lifestream:lifestream@localhost:5433/lifestream"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    speakers_file: str = field(default_factory=lambda: _env("SPEAKERS_FILE", "data/speakers.yaml"))
    search_k: int = field(default_factory=lambda: int(_env("SEARCH_K", "6")))

    # Cloud mode (Stage 3). With S3_BUCKET set, the dashboard server hands out S3 upload links and
    # the GPU work runs on AWS Batch (see cloud/sync.py); without it everything runs locally.
    s3_bucket: str | None = field(default_factory=lambda: _env("S3_BUCKET"))
    aws_region: str | None = field(default_factory=lambda: _env("AWS_REGION", _env("AWS_DEFAULT_REGION")))
    batch_job_queue: str = field(default_factory=lambda: _env("BATCH_JOB_QUEUE", "lifestream-gpu"))
    batch_job_definition: str = field(default_factory=lambda: _env("BATCH_JOB_DEFINITION", "lifestream-worker"))
    # API key for /api/* (X-API-Key header or the dashboard's cookie). Unset = no check (local use).
    api_key: str | None = field(default_factory=lambda: _env("API_KEY"))

    @property
    def parallel_audio(self) -> bool:
        mode = self.parallel_audio_mode.strip().lower()
        if mode == "auto":
            return self.device.startswith("cuda") and self.diarization_device.startswith("cuda")
        return mode in ("1", "true", "yes", "on")

    @property
    def active_llm_model(self) -> str:
        """Model name of the LLM that LLM_PROVIDER selects (recorded in run timelines)."""
        return self.qwen_model if self.llm_provider.lower() == "qwen" else self.llm_model


def get_settings() -> Settings:
    return Settings()
