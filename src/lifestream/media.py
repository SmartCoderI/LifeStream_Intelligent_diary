"""F1: media ingestion and track splitting, using ffprobe / ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .schemas import MediaInfo

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}
AUDIO_SAMPLE_RATE = 16_000  # what Whisper and pyannote expect


class MediaError(Exception):
    """The input file can't be processed (bad format, missing stream, corrupted)."""


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise MediaError(f"'{tool}' is not installed. Install FFmpeg (e.g. `brew install ffmpeg`).")
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _parse_rate(rate: str) -> float:
    num, _, den = rate.partition("/")
    try:
        return float(num) / float(den or 1) if float(den or 1) else 0.0
    except ValueError:
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    """Validate a video file and read its metadata. Raises MediaError on any problem."""
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"File not found: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise MediaError(
            f"Unsupported file type '{path.suffix}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    result = _run([
        _require("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        raise MediaError(f"Can't read {path.name} (corrupted or not a video): {detail}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError(f"{path.name} has no video stream.")
    if audio is None:
        raise MediaError(f"{path.name} has no audio stream; nothing to transcribe.")

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaError(f"{path.name} has zero length.")

    return MediaInfo(
        path=str(path),
        container=fmt.get("format_name", ""),
        duration=duration,
        video_codec=video.get("codec_name", ""),
        audio_codec=audio.get("codec_name", ""),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=_parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"),
        audio_sample_rate=int(audio.get("sample_rate") or 0),
        audio_channels=int(audio.get("channels") or 0),
    )


def extract_audio(video: str | Path, out_wav: str | Path) -> Path:
    """Write the audio track as 16 kHz mono 16-bit WAV."""
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    result = _run([
        _require("ffmpeg"), "-y", "-v", "error", "-i", str(video),
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_wav),
    ])
    if result.returncode != 0 or not out_wav.exists():
        raise MediaError(f"Audio extraction failed: {result.stderr.strip()}")
    return out_wav


def extract_frame(video: str | Path, time_s: float, out_jpg: str | Path, width: int = 768) -> Path:
    """Save the frame at `time_s` as a JPG, scaled to `width` pixels wide."""
    out_jpg = Path(out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    result = _run([
        _require("ffmpeg"), "-y", "-v", "error", "-ss", f"{time_s:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out_jpg),
    ])
    if result.returncode != 0 or not out_jpg.exists():
        raise MediaError(f"Frame extraction at {time_s:.2f}s failed: {result.stderr.strip()}")
    return out_jpg
