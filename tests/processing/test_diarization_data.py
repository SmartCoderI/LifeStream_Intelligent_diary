"""F2 data test: pyannote diarization on a labeled AMI meeting.

Slow (real model on CPU) and needs HF_TOKEN. Run with:
    uv run lifestream download-ami
    uv run pytest -m slow
"""

from pathlib import Path

import pytest

from lifestream.config import get_settings
from lifestream.processing.diarize import diarize
from lifestream.evaluation.metrics import crop, der, load_rttm

AMI = Path("data/ami")
MAX_DER = 0.30
EVAL_SECONDS = 300  # first 5 minutes keeps the CPU run short


@pytest.mark.slow
def test_ami_der_below_threshold(tmp_path):
    settings = get_settings()
    if not settings.hf_token:
        pytest.skip("HF_TOKEN not set")
    wav, rttm = AMI / "ES2004a.wav", AMI / "ES2004a.rttm"
    if not wav.exists():
        pytest.skip("run `uv run lifestream download-ami` first")

    import subprocess
    clip = tmp_path / "clip.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-t", str(EVAL_SECONDS),
                    "-ac", "1", "-ar", "16000", str(clip)], check=True)

    reference = crop(load_rttm(rttm), EVAL_SECONDS)
    hypothesis = diarize(clip, settings)
    error = der(reference, hypothesis)
    print(f"AMI ES2004a first {EVAL_SECONDS}s: DER = {error:.1%} "
          f"({len({s.speaker for s in reference})} true speakers, "
          f"{len({s.speaker for s in hypothesis})} found)")
    assert error < MAX_DER
