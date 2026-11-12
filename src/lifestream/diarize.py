"""F2: speaker diarization ("who spoke when") with pyannote.

Acoustic models only. No LLM is involved in deciding who is speaking.
"""

from __future__ import annotations

import gc
from pathlib import Path

from .config import Settings
from .schemas import SpeakerSegment


class DiarizationError(Exception):
    pass


def diarize(
    wav: str | Path,
    settings: Settings,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> list[SpeakerSegment]:
    if not settings.hf_token:
        raise DiarizationError(
            "HF_TOKEN is not set. Accept the terms for "
            f"{settings.diarization_model} on huggingface.co, create a read token, "
            "and put HF_TOKEN=... in .env"
        )

    import whisperx
    from whisperx.diarize import DiarizationPipeline

    pipeline = DiarizationPipeline(
        model_name=settings.diarization_model,
        token=settings.hf_token,
        device=settings.diarization_device,
    )
    audio = whisperx.load_audio(str(wav))
    df = pipeline(audio, num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers)
    del pipeline
    gc.collect()

    segments = [
        SpeakerSegment(start=float(row.start), end=float(row.end), speaker=str(row.speaker))
        for row in df.itertuples()
    ]
    segments.sort(key=lambda s: (s.start, s.end))
    return segments
