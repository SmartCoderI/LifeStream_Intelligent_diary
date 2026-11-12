"""F4: scene-based keyframe sampling.

Keyframes are taken where the picture changes (scene cuts), not at fixed
intervals. One frame from the middle of each scene; very long scenes get a few
extra frames so a one-camera meeting still has some visual context.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import media
from .schemas import Keyframe, Scene


def detect_scenes(
    video: str | Path,
    duration: float,
    threshold: float = 27.0,
    min_scene_len_s: float = 2.0,
) -> list[Scene]:
    """Return scenes covering the whole video. A video with no cuts is one scene."""
    from scenedetect import ContentDetector, SceneManager, open_video

    stream = open_video(str(video))
    min_frames = max(1, int(round(min_scene_len_s * stream.frame_rate)))
    manager = SceneManager()
    manager.auto_downscale = True  # analyse small frames: much faster, same cuts
    manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_frames))
    manager.detect_scenes(video=stream)

    scenes = [Scene(start=s.get_seconds(), end=e.get_seconds())
              for s, e in manager.get_scene_list(start_in_scene=True)]
    return scenes or [Scene(start=0.0, end=duration)]


def cut_times(scenes: list[Scene]) -> list[float]:
    """Times where one scene ends and the next begins."""
    return [s.start for s in scenes[1:]]


def choose_keyframe_times(
    scenes: list[Scene],
    max_keyframes: int = 20,
    long_scene_s: float = 180.0,
) -> list[float]:
    """Middle of each scene (long scenes split into parts), capped at `max_keyframes`
    by keeping evenly spaced ones."""
    times: list[float] = []
    for s in scenes:
        length = s.end - s.start
        if length <= 0:
            continue
        parts = max(1, math.ceil(length / long_scene_s))
        step = length / parts
        times.extend(s.start + step * (i + 0.5) for i in range(parts))

    if len(times) <= max_keyframes:
        return times
    if max_keyframes <= 0:
        return []
    if max_keyframes == 1:
        return [times[len(times) // 2]]
    stride = (len(times) - 1) / (max_keyframes - 1)
    return [times[round(i * stride)] for i in range(max_keyframes)]


def run_visual_branch(
    video: str,
    out_dir: str,
    duration: float,
    threshold: float,
    min_scene_len_s: float,
    max_keyframes: int,
    keyframe_width: int,
) -> tuple[list[Scene], list[Keyframe]]:
    """Scene detection + keyframe extraction. Top-level so it can run in a separate process."""
    scenes = detect_scenes(video, duration, threshold, min_scene_len_s)
    frames_dir = Path(out_dir) / "keyframes"
    frames_dir.mkdir(parents=True, exist_ok=True)
    keyframes: list[Keyframe] = []
    for t in choose_keyframe_times(scenes, max_keyframes):
        name = f"frame_{int(t * 1000):09d}.jpg"
        media.extract_frame(video, t, frames_dir / name, width=keyframe_width)
        keyframes.append(Keyframe(time=t, path=f"keyframes/{name}"))
    return scenes, keyframes
