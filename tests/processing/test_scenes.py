"""F4: scene detection against known cut times, and keyframe selection."""

import pytest

from lifestream.processing import media, scenes
from lifestream.schemas import Scene

TOLERANCE = 0.5  # a detected cut counts if within 0.5 s of a real one


def match_cuts(true_cuts, found):
    remaining = list(found)
    hits = 0
    for c in true_cuts:
        near = [f for f in remaining if abs(f - c) <= TOLERANCE]
        if near:
            hits += 1
            remaining.remove(min(near, key=lambda f: abs(f - c)))
    precision = hits / len(found) if found else 0.0
    recall = hits / len(true_cuts) if true_cuts else 1.0
    return precision, recall


def test_detects_known_cuts(scene_video):
    path, true_cuts = scene_video
    duration = media.probe(path).duration
    found = scenes.cut_times(scenes.detect_scenes(path, duration, min_scene_len_s=2.0))
    precision, recall = match_cuts(true_cuts, found)
    print(f"true={true_cuts} found={[round(f, 2) for f in found]} P={precision:.2f} R={recall:.2f}")
    assert precision >= 0.8 and recall >= 0.8


def test_scenes_cover_whole_video(scene_video):
    path, _ = scene_video
    duration = media.probe(path).duration
    found = scenes.detect_scenes(path, duration)
    assert found[0].start == pytest.approx(0, abs=0.1)
    assert found[-1].end == pytest.approx(duration, abs=0.2)


def test_sampled_frames_find_the_same_cuts(scene_video):
    path, true_cuts = scene_video
    duration = media.probe(path).duration
    found = scenes.detect_scenes(path, duration, min_scene_len_s=2.0, sample_fps=5)
    precision, recall = match_cuts(true_cuts, scenes.cut_times(found))
    assert precision >= 0.8 and recall >= 0.8
    assert found[0].start == pytest.approx(0, abs=0.1) and found[-1].end == pytest.approx(duration, abs=0.01)


def test_keyframes_one_per_scene_middle():
    times = scenes.choose_keyframe_times([Scene(start=0, end=4), Scene(start=4, end=10)])
    assert times == [2.0, 7.0]


def test_long_scene_gets_extra_frames():
    times = scenes.choose_keyframe_times([Scene(start=0, end=600)], long_scene_s=180)
    assert len(times) == 4  # ceil(600 / 180)


def test_keyframe_cap_keeps_evenly_spaced():
    many = [Scene(start=i, end=i + 1) for i in range(100)]
    times = scenes.choose_keyframe_times(many, max_keyframes=20)
    assert len(times) == 20
    assert times[0] == 0.5 and times[-1] == 99.5
    assert times == sorted(times)


def test_visual_branch_writes_keyframes(scene_video, tmp_path):
    path, true_cuts = scene_video
    duration = media.probe(path).duration
    found, keyframes = scenes.run_visual_branch(str(path), str(tmp_path), duration, 27.0, 2.0, 20, 320)
    assert len(keyframes) == len(found)
    assert all((tmp_path / k.path).exists() for k in keyframes)
