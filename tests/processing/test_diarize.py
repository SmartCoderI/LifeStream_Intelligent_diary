"""F2 wiring: pyannote's stage timings and the window-step setting."""

import os

import pytest

from lifestream.processing.diarize import StageClock, set_window_step


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_stage_clock_splits_segmentation_embeddings_clustering():
    clock = FakeClock()
    stages = StageClock(clock)
    for t, name in [(101, "segmentation"), (103, "segmentation"), (103.5, "speaker_counting"),
                    (110, "embeddings"), (120, "embeddings"), (121, "discrete_diarization")]:
        clock.t = t
        stages.hook(name, None, completed=1, total=2)
    clock.t = 122
    stages.finish()
    assert stages.spans() == [
        ("segmentation", "Find speech + overlapping voices", 100, 103.5),
        ("embeddings", "Voice embeddings", 103.5, 120),
        ("clustering", "Group voices into speakers (clustering)", 120, 122),
    ]


def test_stage_clock_without_speech_reports_nothing():
    stages = StageClock(FakeClock())
    stages.hook("segmentation")
    stages.finish()
    assert stages.spans() == []


def test_window_step_reaches_the_sliding_window():
    """set_window_step uses pyannote internals; this fails if a pyannote upgrade moves them."""
    from lifestream.config import get_settings

    settings = get_settings()
    if not settings.hf_token and os.environ.get("HF_HUB_OFFLINE") != "1":
        pytest.skip("HF_TOKEN not set")
    from pyannote.audio import Pipeline

    try:
        pipeline = Pipeline.from_pretrained(settings.diarization_model, token=settings.hf_token)
    except Exception as e:  # noqa: BLE001 - model not downloaded / no network
        pytest.skip(f"pyannote model unavailable: {e}")
    assert pipeline._segmentation.step == pytest.approx(0.1 * pipeline._segmentation.duration)
    set_window_step(pipeline, 0.2)
    assert pipeline.segmentation_step == 0.2
    assert pipeline._segmentation.step == pytest.approx(0.2 * pipeline._segmentation.duration)
