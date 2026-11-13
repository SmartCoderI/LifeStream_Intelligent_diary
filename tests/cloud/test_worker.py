"""F7 worker: S3 in -> pipeline -> S3 out, with a fake S3 client and a fake pipeline."""

import datetime as dt
import json
from pathlib import Path

import pytest

from lifestream.cloud.worker import parse_s3_uri, resolve_date, run_job
from lifestream.config import Settings


class FakeS3:
    def __init__(self):
        self.downloads, self.uploads, self.puts = [], {}, {}

    def download_file(self, bucket, key, filename):
        self.downloads.append((bucket, key))
        Path(filename).write_bytes(b"video bytes")

    def upload_file(self, filename, bucket, key):
        self.uploads[(bucket, key)] = Path(filename).read_bytes()

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.puts[(Bucket, Key)] = json.loads(Body)


def fake_process(calls):
    def process(video, date, out_root, settings, **kw):
        calls.append({"video": video, "date": date, **kw})
        folder = Path(out_root) / "team-sync"
        (folder / "keyframes").mkdir(parents=True)
        (folder / "script.md").write_text("script")
        (folder / "keyframes" / "frame_000001000.jpg").write_bytes(b"jpg")
        return folder
    return process


def test_parse_s3_uri():
    assert parse_s3_uri("s3://bucket/uploads/a b.mp4") == ("bucket", "uploads/a b.mp4")
    assert parse_s3_uri("s3://bucket/out/") == ("bucket", "out/")
    with pytest.raises(ValueError):
        parse_s3_uri("/local/file.mp4")


def test_resolve_date():
    assert resolve_date("today") == dt.date.today().isoformat()
    assert resolve_date("") == dt.date.today().isoformat()
    assert resolve_date("2026-11-12") == "2026-11-12"
    with pytest.raises(ValueError):
        resolve_date("12/11/2026")


def test_run_job_downloads_processes_and_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_BATCH_JOB_ID", "job-123")
    s3, calls = FakeS3(), []
    report = run_job("s3://bkt/uploads/Team Sync.mp4", "s3://bkt/out/", "2026-11-12", Settings(),
                     s3=s3, work_dir=tmp_path, run_llm=False, num_speakers=3,
                     process=fake_process(calls))

    assert s3.downloads == [("bkt", "uploads/Team Sync.mp4")]
    (call,) = calls
    assert call["video"].name == "Team Sync.mp4" and call["date"] == "2026-11-12"
    assert call["run_llm"] is False and call["num_speakers"] == 3 and call["source"] == "batch"
    (span,) = call["extra_spans"]
    assert span["name"] == "s3_download" and span["kind"] == "io" and span["meta"]["bytes"] == 11

    assert set(s3.uploads) == {("bkt", "out/team-sync/script.md"),
                               ("bkt", "out/team-sync/keyframes/frame_000001000.jpg")}
    saved = s3.puts[("bkt", "out/team-sync/worker.json")]
    assert saved == report
    assert report["job_id"] == "job-123" and report["output"] == "s3://bkt/out/team-sync/"
    assert report["files_uploaded"] == 2
    assert {"download_s", "pipeline_s", "upload_s"} <= set(report)


def test_run_job_failure_propagates(tmp_path):
    def broken(*a, **kw):
        raise RuntimeError("CUDA out of memory")

    s3 = FakeS3()
    with pytest.raises(RuntimeError, match="CUDA"):
        run_job("s3://bkt/uploads/x.mp4", "s3://bkt/out/", "today", Settings(), s3=s3,
                work_dir=tmp_path, process=broken)
    assert not s3.uploads and not s3.puts
