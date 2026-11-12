import shutil

import pytest

from lifestream import testdata

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(scope="session")
def format_samples(tmp_path_factory):
    return testdata.make_format_samples(tmp_path_factory.mktemp("formats"))


@pytest.fixture(scope="session")
def scene_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("scenes") / "cuts.mp4"
    cuts = testdata.make_scene_video(path)
    return path, cuts
