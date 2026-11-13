"""F7 worker: S3 in -> pipeline -> S3 out, with a fake S3 client and a fake pipeline."""

import datetime as dt
import json
import time
from pathlib import Path

import pytest

from lifestream.cloud.worker import parse_s3_uri, resolve_date, run_job, startup_times, video_id_from_key
from lifestream.config import Settings


class FakeS3:
    def __init__(self):
        self.downloads, self.uploads, self.puts = [], {}, {}
        self.history, self.metadata = [], {}

    def download_file(self, bucket, key, filename):
        self.downloads.append((bucket, key))
        Path(filename).write_bytes(b"video bytes")

    def upload_file(self, filename, bucket, key):
        self.uploads[(bucket, key)] = Path(filename).read_bytes()

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.puts[(Bucket, Key)] = json.loads(Body)
        self.history.append(json.loads(Body))

    def head_object(self, *, Bucket, Key):
        if Key not in self.metadata:
            raise KeyError(Key)
        return {"Metadata": self.metadata[Key]}


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
    assert not s3.uploads
    assert set(s3.puts) == {("bkt", "out/x/status.json")}  # no results, but the failure is reported
    status = s3.puts[("bkt", "out/x/status.json")]
    assert status["status"] == "failed" and status["error"] == "CUDA out of memory"


def test_failed_job_keeps_its_partial_timeline(tmp_path):
    def fails_mid_way(video, date, out_root, settings, *, video_id, **kw):
        folder = Path(out_root) / video_id
        folder.mkdir(parents=True)
        (folder / "timeline.json").write_text('{"status": "failed", "spans": []}')
        raise RuntimeError("diarization crashed")

    s3 = FakeS3()
    with pytest.raises(RuntimeError):
        run_job("s3://bkt/uploads/x/x.mp4", "s3://bkt/out/", "today", Settings(), s3=s3,
                work_dir=tmp_path, process=fails_mid_way)
    assert set(s3.uploads) == {("bkt", "out/x/timeline.json")}
    assert s3.puts[("bkt", "out/x/status.json")]["status"] == "failed"


def test_resolve_date_prefers_the_uploaded_date():
    assert resolve_date("today", "2026-11-01") == "2026-11-01"
    assert resolve_date("2026-11-12", "2026-11-01") == "2026-11-12"   # an explicit job parameter wins
    with pytest.raises(ValueError):
        resolve_date("today", "yesterday")


def test_video_id_from_key():
    assert video_id_from_key("uploads/team-sync-2/team-sync-2.mp4") == "team-sync-2"
    assert video_id_from_key("uploads/Team Sync.mp4") is None
    assert video_id_from_key("uploads/Bad ID/x.mp4") is None
    assert video_id_from_key("raw/team-sync/x.mp4") is None


def test_dashboard_upload_uses_its_video_id_date_and_reports_each_step(tmp_path):
    """F11: an upload from the dashboard (uploads/<id>/<file>, date in S3 metadata)."""
    s3, seen = FakeS3(), {}
    key = "uploads/team-sync-2/team-sync-2.mp4"
    s3.metadata[key] = {"date": "2026-11-03"}

    def process(video, date, out_root, settings, *, video_id, on_step, **kw):
        seen.update(video_id=video_id, date=date)
        for step in ("validating", "transcribing", "diarizing", "detecting_scenes", "classifying",
                     "summarizing", "indexing"):
            on_step(step)
        folder = Path(out_root) / video_id
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text("{}")
        return folder

    run_job(f"s3://bkt/{key}", "s3://bkt/out/", "today", Settings(), s3=s3, work_dir=tmp_path, process=process)

    assert seen == {"video_id": "team-sync-2", "date": "2026-11-03"}
    assert ("bkt", "out/team-sync-2/summary.json") in s3.uploads
    steps = [h["step"] for h in s3.history if "step" in h]
    # the status file went through every step in order, ending with done (indexing is the server's job)
    assert steps == ["validating", "validating", "transcribing", "diarizing", "detecting_scenes",
                     "classifying", "summarizing", "uploading_results", "done"]
    final = s3.puts[("bkt", "out/team-sync-2/status.json")]
    assert final["status"] == "done" and final["date"] == "2026-11-03" and final["error"] is None
    assert set(final["step_timings"]) == {"validating", "transcribing", "diarizing", "detecting_scenes",
                                          "classifying", "summarizing", "uploading_results"}
    assert all(t >= 0 for t in final["step_timings"].values())


def test_startup_times_from_uptime_and_ecs_metadata(tmp_path):
    uptime = tmp_path / "uptime"
    uptime.write_text("120.50 400.00\n")
    responses = {
        "http://meta/v4/task": {"PullStartedAt": "2026-11-13T05:00:10.123456789Z",
                                "PullStoppedAt": "2026-11-13T05:04:40Z"},
        "http://meta/v4": {"StartedAt": "2026-11-13T05:05:00Z"},
    }
    times = startup_times(uptime, "http://meta/v4", fetch=responses.__getitem__)
    assert times["host_booted_at"] == pytest.approx(time.time() - 120.5, abs=2)
    assert times["pull_stopped_at"] - times["pull_started_at"] == pytest.approx(269.877, abs=0.01)
    assert times["container_started_at"] == dt.datetime(2026, 11, 13, 5, 5, tzinfo=dt.timezone.utc).timestamp()


def test_startup_times_outside_aws_is_empty(tmp_path):
    assert startup_times(tmp_path / "missing", "") == {}
