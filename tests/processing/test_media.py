"""F1: format validation and stream separation."""

import wave

import pytest

from lifestream.processing import media


@pytest.mark.parametrize("fmt", ["mp4", "mov", "mkv"])
def test_valid_formats_pass(format_samples, fmt):
    info = media.probe(format_samples[fmt])
    assert info.duration == pytest.approx(3.0, abs=0.2)
    assert info.width == 320 and info.height == 240
    assert info.fps == pytest.approx(30, abs=0.1)
    assert info.audio_channels == 2


@pytest.mark.parametrize("name, message", [
    ("no_audio", "no audio stream"),
    ("truncated", "corrupted"),
    ("not_a_video", "corrupted"),
    ("wrong_extension", "Unsupported file type"),
])
def test_bad_files_rejected_with_clear_error(format_samples, name, message):
    with pytest.raises(media.MediaError, match=message):
        media.probe(format_samples[name])


def test_missing_file(tmp_path):
    with pytest.raises(media.MediaError, match="not found"):
        media.probe(tmp_path / "nope.mp4")


@pytest.mark.parametrize("fmt", ["mp4", "mkv"])
def test_audio_is_16k_mono_and_same_length(format_samples, tmp_path, fmt):
    wav = media.extract_audio(format_samples[fmt], tmp_path / "a.wav")
    with wave.open(str(wav)) as w:
        assert w.getframerate() == 16_000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        seconds = w.getnframes() / w.getframerate()
    assert seconds == pytest.approx(media.probe(format_samples[fmt]).duration, abs=0.1)


def test_extract_frame(format_samples, tmp_path):
    jpg = media.extract_frame(format_samples["mp4"], 1.0, tmp_path / "f.jpg", width=160)
    assert jpg.stat().st_size > 0
