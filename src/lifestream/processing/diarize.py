"""F2: speaker diarization ("who spoke when") with pyannote.

Acoustic models only. No LLM is involved in deciding who is speaking.

FUTURE (after moving to AWS with an NVIDIA GPU): consider NVIDIA's
`nvidia/Nemotron-3-Diarization` (released 2026-09, ~100M params, OpenMDW 1.1, commercial use OK).
  * Why: lower reported error on meeting audio, especially overlapping speech
    (NVIDIA's numbers, e.g. AMI single-mic DER 11.14%; no direct comparison with pyannote).
  * Why not now: built for NVIDIA GPUs on Linux; needs NeMo 3.0 and Python >= 3.12
    (this project is on 3.11, and NeMo's PyTorch may clash with WhisperX). Max 8 speakers.
  * How to switch: add a DIARIZATION_BACKEND setting (pyannote | nemotron), the same way
    LLM_PROVIDER picks the LLM, and a second function here that returns the same
    list[SpeakerSegment] ("speaker X from A to B"). merge.py and everything after it stay as-is.
  * Before deciding: run the M1 evaluation (labels/) with both backends on our own videos.
"""

from __future__ import annotations

import gc
import os
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
    # The cloud worker image has the model baked in and runs with HF_HUB_OFFLINE=1,
    # so it needs no token at run time (the token is only used at image build time).
    if not settings.hf_token and os.environ.get("HF_HUB_OFFLINE") != "1":
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
