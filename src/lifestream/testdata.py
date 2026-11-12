"""Generates test data with known answers.

- F1: small videos in several formats, plus broken ones.
- F4: a video with scene cuts at known times.
- M1: a 3-speaker conversation with overlapping speech and exact speaker labels,
  made with macOS text-to-speech voices (`say`). Synthetic voices are easier than
  real ones, so treat this as a development check; the real M1 check should use a
  hand-labeled recording when you have one.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from .evals import write_rttm
from .schemas import SpeakerSegment

SR = 16_000


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


# ---------- F1 ----------
def make_format_samples(out_dir: str | Path, seconds: float = 3.0) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    video = f"testsrc2=size=320x240:rate=30:duration={seconds}"
    audio = f"sine=frequency=440:sample_rate=44100:duration={seconds}"
    paths: dict[str, Path] = {}
    for ext, vcodec, acodec in [("mp4", "libx264", "aac"), ("mov", "libx264", "aac"), ("mkv", "libx264", "libopus")]:
        p = out / f"sample.{ext}"
        _ffmpeg("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio, "-ac", "2",
                "-c:v", vcodec, "-pix_fmt", "yuv420p", "-c:a", acodec, "-shortest", str(p))
        paths[ext] = p

    p = out / "no_audio.mp4"
    _ffmpeg("-f", "lavfi", "-i", video, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
    paths["no_audio"] = p

    p = out / "truncated.mp4"  # first 40% of a valid file: header cut off mid-stream
    data = paths["mp4"].read_bytes()
    p.write_bytes(data[: int(len(data) * 0.4)])
    paths["truncated"] = p

    p = out / "not_a_video.mp4"
    p.write_text("this is not a video")
    paths["not_a_video"] = p

    p = out / "notes.txt"
    p.write_text("unsupported extension")
    paths["wrong_extension"] = p
    return paths


# ---------- F4 ----------
DEFAULT_CLIPS = [
    ("testsrc2", 4.0),
    ("smptebars", 3.0),
    ("color=c=darkgreen", 5.0),
    ("yuvtestsrc", 4.0),
    ("color=c=navy", 3.0),
    ("rgbtestsrc", 4.0),
]


def make_scene_video(path: str | Path, clips: list[tuple[str, float]] = DEFAULT_CLIPS,
                     size: str = "640x360", fps: int = 30) -> list[float]:
    """Join visually different clips. Returns the true cut times (seconds)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    for src, dur in clips:
        sep = ":" if "=" in src else "="
        args += ["-f", "lavfi", "-i", f"{src}{sep}size={size}:rate={fps}:duration={dur}"]
    total = sum(d for _, d in clips)
    args += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=44100:duration={total}"]
    n = len(clips)
    chains = "".join(f"[{i}:v]format=yuv420p,setsar=1[v{i}];" for i in range(n))
    concat = "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    _ffmpeg(*args, "-filter_complex", chains + concat, "-map", "[v]", "-map", f"{n}:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
    cuts, t = [], 0.0
    for _, dur in clips[:-1]:
        t += dur
        cuts.append(t)
    return cuts


# ---------- M1: 3-speaker overlapping conversation ----------
# (speaker index, text, overlap): overlap > 0 means this line starts that many
# seconds before the previous line ends (people talking over each other).
CONVERSATION = [
    (0, "Okay, let's get started. This week we need to decide what happens with the frontend redesign.", 0),
    (1, "I finished the new navigation bar on Monday, but the mobile layout still breaks below four hundred pixels.", 0),
    (2, "Right, I saw that. Is it the flexbox wrapping or the fixed width on the logo?", 0.8),
    (1, "It's the logo. I hard coded the width, which was a mistake.", 0),
    (0, "Can you fix that before Thursday? Marketing wants a demo on Friday morning.", 1.0),
    (1, "Yes, that's a small change. I'll have it done tomorrow.", 0),
    (2, "While we're on the frontend, the dashboard charts are loading really slowly.", 0),
    (0, "How slow are we talking?", 0.7),
    (2, "About four seconds on first load. Most of it is the charting library bundle.", 0),
    (1, "We could lazy load the charts so the page shows up first.", 1.2),
    (2, "Exactly, that was my plan. I'll split the bundle and measure again.", 0.6),
    (0, "Good. Please post the numbers in the channel when you have them.", 0),
    (1, "One more thing, the design team sent new colors for dark mode.", 0),
    (0, "Do they match the accessibility contrast rules?", 0.9),
    (1, "Mostly. Two of the gray text colors are too light on the dark background.", 0),
    (2, "I can check them with the contrast tool after lunch.", 1.0),
    (0, "Great. So, to recap: logo fix by tomorrow, chart lazy loading this week, and a contrast check today.", 0),
    (1, "Sounds right to me.", 0.8),
    (2, "Same here. Let's meet again on Thursday afternoon.", 0.5),
    (0, "Perfect. Thanks, everyone.", 0),
]
VOICES = ["Samantha", "Daniel", "Karen"]


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def _trim(audio: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    idx = np.flatnonzero(np.abs(audio) > threshold)
    return audio[idx[0]: idx[-1] + 1] if idx.size else audio


def make_overlap_conversation(out_dir: str | Path, voices: list[str] = VOICES,
                              pause: float = 0.4) -> dict[str, Path]:
    """Writes conversation.wav, conversation.mp4 and conversation.rttm (exact labels)."""
    if shutil.which("say") is None:
        raise RuntimeError("This generator needs macOS `say`.")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    clips: list[tuple[int, np.ndarray, float]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (spk, text, overlap) in enumerate(CONVERSATION):
            aiff, wav = Path(tmp) / f"{i}.aiff", Path(tmp) / f"{i}.wav"
            subprocess.run(["say", "-v", voices[spk], "-o", str(aiff), text], check=True)
            _ffmpeg("-i", str(aiff), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(wav))
            clips.append((spk, _trim(_read_wav(wav)), overlap))

    placed: list[tuple[int, float, np.ndarray]] = []
    t = 0.5
    prev_end = 0.0
    for spk, audio, overlap in clips:
        start = prev_end - overlap if overlap > 0 and placed else (prev_end + pause if placed else t)
        start = max(start, 0.0)
        placed.append((spk, start, audio))
        prev_end = start + len(audio) / SR

    total = int((prev_end + 1.0) * SR)
    mix = np.zeros(total, dtype=np.float32)
    labels: list[SpeakerSegment] = []
    for spk, start, audio in placed:
        i = int(start * SR)
        mix[i: i + len(audio)] += audio
        labels.append(SpeakerSegment(start=start, end=start + len(audio) / SR, speaker=f"spk{spk}"))
    mix /= max(1.0, float(np.abs(mix).max()) / 0.9)

    wav_path = out / "conversation.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())

    rttm_path = out / "conversation.rttm"
    write_rttm(rttm_path, labels, file_id="conversation")

    mp4_path = out / "conversation.mp4"
    dur = total / SR
    _ffmpeg("-f", "lavfi", "-i", f"color=c=gray:size=640x360:rate=30:duration={dur}",
            "-i", str(wav_path), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(mp4_path))
    return {"wav": wav_path, "rttm": rttm_path, "mp4": mp4_path}


# ---------- F2 data test: one labeled AMI meeting ----------
AMI_AUDIO = "https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{m}/audio/{m}.Mix-Headset.wav"
AMI_RTTM = "https://raw.githubusercontent.com/pyannote/AMI-diarization-setup/main/only_words/rttms/test/{m}.rttm"


def download_ami(out_dir: str | Path, meeting: str = "ES2004a") -> dict[str, Path]:
    import urllib.request

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"wav": out / f"{meeting}.wav", "rttm": out / f"{meeting}.rttm"}
    for key, url in [("wav", AMI_AUDIO), ("rttm", AMI_RTTM)]:
        if not paths[key].exists():
            urllib.request.urlretrieve(url.format(m=meeting), paths[key])
    return paths
