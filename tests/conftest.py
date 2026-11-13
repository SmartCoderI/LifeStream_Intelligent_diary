import shutil

import pytest

from lifestream.evaluation import testdata

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(scope="session")
def format_samples(tmp_path_factory):
    return testdata.make_format_samples(tmp_path_factory.mktemp("formats"))


@pytest.fixture(scope="session")
def scene_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("scenes") / "cuts.mp4"
    cuts = testdata.make_scene_video(path)
    return path, cuts


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Tests never touch the real speaker registry or search database."""
    monkeypatch.setenv("SPEAKERS_FILE", str(tmp_path / "speakers.yaml"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/none")
    for name in ("S3_BUCKET", "API_KEY"):  # cloud mode only where a test asks for it
        monkeypatch.setenv(name, "")


BUCKET = "lifestream-test-bucket"


@pytest.fixture
def s3(monkeypatch):
    """A mocked S3 (moto) with an empty bucket, in us-west-1 like the real one."""
    from moto import mock_aws
    import boto3
    from botocore.config import Config

    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-1")
    with mock_aws():
        client = boto3.client("s3", region_name="us-west-1", config=Config(signature_version="s3v4"))
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-west-1"})
        yield client
