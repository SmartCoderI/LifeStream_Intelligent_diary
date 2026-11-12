"""F2: speaker diarization ("who spoke when") with pyannote.

Acoustic models only. No LLM is involved in deciding who is speaking.
"""

from __future__ import annotations

import gc
from contextlib import nullcontext
from pathlib import Path

from ..config import Settings
from ..schemas import SpeakerSegment


class DiarizationError(Exception):
    pass


def diarize(
    wav: str | Path,
    settings: Settings,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    timings=None,
) -> list[SpeakerSegment]:
    if not settings.hf_token:
        raise DiarizationError(
            "HF_TOKEN is not set. Accept the terms for "
            f"{settings.diarization_model} on huggingface.co, create a read token, "
            "and put HF_TOKEN=... in .env"
        )

    import whisperx
    from whisperx.diarize import DiarizationPipeline

    def sub(name: str, label: str, kind: str = "step"):
        if timings is None:
            return nullcontext({"meta": {}})
        return timings.step(f"diarize.{name}", label, kind=kind, parent="diarize")

    with sub("load_model", "Load speaker model (pyannote)", "load"):
        pipeline = DiarizationPipeline(
            model_name=settings.diarization_model,
            token=settings.hf_token,
            device=settings.diarization_device,
        )
    audio = whisperx.load_audio(str(wav))
    with sub("run", "Identify speakers") as span:
        df = pipeline(audio, num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers)
        span["meta"]["speakers"] = int(df["speaker"].nunique()) if len(df) else 0
    del pipeline
    gc.collect()

    segments = [
        SpeakerSegment(start=float(row.start), end=float(row.end), speaker=str(row.speaker))
        for row in df.itertuples()
    ]
    segments.sort(key=lambda s: (s.start, s.end))
    return segments
