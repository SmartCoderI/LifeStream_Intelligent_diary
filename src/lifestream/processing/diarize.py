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
import time
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

    import torch
    import whisperx
    from pyannote.audio import Pipeline

    def sub(name: str, label: str, kind: str = "step"):
        if timings is None:
            return nullcontext({"meta": {}})
        return timings.step(f"diarize.{name}", label, kind=kind, parent="diarize")

    with sub("load_model", "Load speaker model (pyannote)", "load"):
        pipeline = Pipeline.from_pretrained(settings.diarization_model, token=settings.hf_token)
        pipeline.to(torch.device(settings.diarization_device))
        set_window_step(pipeline, settings.diarization_step)
    audio = whisperx.load_audio(str(wav))
    stages = StageClock()
    output = pipeline({"waveform": torch.from_numpy(audio[None, :]), "sample_rate": SAMPLE_RATE},
                      num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers,
                      hook=stages.hook)
    stages.finish()
    if timings is not None:
        for name, label, start, end in stages.spans():
            timings.add(f"diarize.{name}", label, start, end, parent="diarize")
    del pipeline
    gc.collect()

    segments = [SpeakerSegment(start=float(seg.start), end=float(seg.end), speaker=str(speaker))
                for seg, _, speaker in output.speaker_diarization.itertracks(yield_label=True)]
    segments.sort(key=lambda s: (s.start, s.end))
    return segments


SAMPLE_RATE = 16_000  # whisperx.load_audio's rate


def set_window_step(pipeline, step: float) -> None:
    """How far pyannote's 10 s analysis window moves each time, as a share of its length.
    0.1 (pyannote's default) = every second of audio is analysed ~10 times; 0.2 halves the
    windows and the voice embeddings computed for them, which is most of the time.
    pyannote only takes this when the pipeline is built, so it is also set on the
    segmentation model's sliding window (checked by tests/processing/test_diarize.py)."""
    pipeline.segmentation_step = step
    pipeline._segmentation.step = step * pipeline._segmentation.duration


class StageClock:
    """pyannote's progress hook -> wall-clock time per stage. pyannote calls the hook during
    a stage (with progress) and once when it is done; clustering runs between the end of
    "embeddings" and "discrete_diarization", so its time is the gap between them."""

    LABELS = {"segmentation": "Find speech + overlapping voices", "embeddings": "Voice embeddings",
              "clustering": "Group voices into speakers (clustering)"}

    def __init__(self, clock=time.time):
        self.clock = clock
        self.start = clock()
        self.last: dict[str, float] = {}  # stage -> time of its last hook call
        self.end: float | None = None

    def hook(self, step_name, step_artifact=None, file=None, total=None, completed=None) -> None:
        self.last[step_name] = self.clock()

    def finish(self) -> None:
        self.end = self.clock()

    def spans(self) -> list[tuple[str, str, float, float]]:
        """(name, label, start, end) for segmentation, embeddings, clustering (+ the rest)."""
        seg_end = self.last.get("speaker_counting", self.last.get("segmentation"))
        emb_end = self.last.get("embeddings")
        if seg_end is None or emb_end is None or self.end is None:
            return []  # no speech: pyannote stops early
        return [("segmentation", self.LABELS["segmentation"], self.start, seg_end),
                ("embeddings", self.LABELS["embeddings"], seg_end, emb_end),
                ("clustering", self.LABELS["clustering"], emb_end, self.end)]
