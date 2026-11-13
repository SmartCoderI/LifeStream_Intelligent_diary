"""F11 (server side): following a cloud video from upload to done, with moto S3 and a fake Batch."""

import json
import time

import pytest

from lifestream.api.jobs import read_status, write_status
from lifestream.cloud.sync import CloudStore, CloudSync
from lifestream.config import Settings
from lifestream.memory.store import MemoryUnavailable
from lifestream.pipeline import Components, process_video

from conftest import BUCKET
from fakes import FakeBatch, FakeLLM, fake_diarize, fake_index, fake_transcribe, fake_visual, upload_folder

VID = "team-sync"
KEY = f"uploads/{VID}/{VID}.mp4"


@pytest.fixture
def worker_results(tmp_path, format_samples):
    """A real results folder, as the GPU worker would produce it (fake models)."""
    components = Components(transcribe=fake_transcribe, diarize=fake_diarize, visual=fake_visual,
                            index=lambda out, s: None)
    return process_video(format_samples["mp4"], "2026-11-04", tmp_path / "worker", Settings(),
                         llm=FakeLLM("meeting"), components=components, parallel=False,
                         video_id=VID, log=lambda m: None)


def make_sync(tmp_path, s3, batch=None, index_fn=fake_index):
    store = CloudStore(BUCKET, s3, batch if batch is not None else FakeBatch())
    return CloudSync(tmp_path / "out", Settings(), store, index_fn, log=lambda m: None)


def start_upload(sync, **extra):
    now = time.time()
    write_status(sync.out_root / VID, status="queued", step="uploading", progress=0, s3_key=KEY,
                 date="2026-11-04", created_at=now, upload_started_at=now - 5, original_name="Team Sync.mp4",
                 size_bytes=10, **extra)
    return sync.out_root / VID


def worker_says(s3, **fields):
    s3.put_object(Bucket=BUCKET, Key=f"out/{VID}/status.json", Body=json.dumps(fields).encode())


def test_waits_for_the_upload_then_queues(tmp_path, s3):
    sync = make_sync(tmp_path, s3)
    folder = start_upload(sync)
    sync.tick()
    assert read_status(folder)["step"] == "uploading"

    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"video", Metadata={"date": "2026-11-04"})
    sync.tick()
    status = read_status(folder)
    assert status["status"] == "queued" and status["step"] == "queued" and status["uploaded_at"]


def test_copies_the_workers_progress(tmp_path, s3):
    sync = make_sync(tmp_path, s3)
    folder = start_upload(sync, uploaded_at=time.time())
    worker_says(s3, status="processing", step="diarizing", progress=45, started_at=1.0, step_started_at=2.0,
                step_timings={"validating": 0.3, "transcribing": 61.2}, job_id="job-7")
    sync.tick()
    status = read_status(folder)
    assert (status["status"], status["step"], status["progress"]) == ("processing", "diarizing", 45)
    assert status["step_timings"] == {"validating": 0.3, "transcribing": 61.2}
    assert status["job_id"] == "job-7"


def test_done_downloads_indexes_and_records_the_run(tmp_path, s3, worker_results):
    indexed = []
    sync = make_sync(tmp_path, s3, index_fn=lambda out, s: indexed.append(out.name) or 5)
    uploaded = time.time() - 300
    folder = start_upload(sync, uploaded_at=uploaded)
    write_status(folder, upload_started_at=uploaded - 30)
    upload_folder(s3, BUCKET, worker_results, f"out/{VID}/")
    worker_says(s3, status="done", step="done", started_at=uploaded + 120, finished_at=time.time(),
                step_timings={"transcribing": 60.0, "diarizing": 20.0, "summarizing": 9.0}, job_id="job-1")

    sync.tick()

    status = read_status(folder)
    assert status["status"] == "done" and status["progress"] == 100 and status["index_error"] is None
    assert status["s3_key"] == KEY  # our status file, not the worker's
    assert set(status["step_timings"]) == {"transcribing", "diarizing", "summarizing", "indexing"}
    assert indexed == [VID]
    for name in ("summary.json", "summary.md", "script.json", "metadata.json", "timeline.json"):
        assert (folder / name).exists(), name
    assert (sync.out_root / "diaries" / "2026-11-04.md").exists()  # the day's diary is rebuilt here

    (run,) = [json.loads(line) for line in (sync.out_root / "_logs" / "runs.jsonl").read_text().splitlines()]
    names = [s["name"] for s in run["spans"]]
    assert {"upload", "queue", "index"} <= set(names) and run["status"] == "done" and run["source"] == "cloud"
    spans = {s["name"]: s for s in run["spans"]}
    assert spans["upload"]["duration"] == pytest.approx(30, abs=1)
    assert spans["queue"]["duration"] == pytest.approx(120, abs=1)  # upload done -> worker started (cold start)


def test_worker_failure_is_reported(tmp_path, s3):
    sync = make_sync(tmp_path, s3)
    folder = start_upload(sync, uploaded_at=time.time())
    worker_says(s3, status="failed", step="failed", error="CUDA out of memory", step_timings={"validating": 1})
    sync.tick()
    status = read_status(folder)
    assert status["status"] == "failed" and status["error"] == "CUDA out of memory"


def test_job_that_fails_before_starting_is_reported(tmp_path, s3):
    batch = FakeBatch()
    batch.jobs.append({"jobId": "job-9", "status": "FAILED", "createdAt": 5,
                       "statusReason": "CannotPullContainerError: image not found",
                       "parameters": {"input": f"s3://{BUCKET}/{KEY}"}})
    sync = make_sync(tmp_path, s3, batch=batch)
    folder = start_upload(sync, uploaded_at=time.time())
    write_status(folder, step="queued")
    sync.tick()
    status = read_status(folder)
    assert status["status"] == "failed" and "CannotPullContainerError" in status["error"]


def test_search_down_still_finishes_the_diary(tmp_path, s3, worker_results):
    def index_down(out, settings):
        raise MemoryUnavailable("database unreachable")

    sync = make_sync(tmp_path, s3, index_fn=index_down)
    folder = start_upload(sync, uploaded_at=time.time())
    upload_folder(s3, BUCKET, worker_results, f"out/{VID}/")
    worker_says(s3, status="done", step="done", started_at=time.time(), step_timings={})
    sync.tick()
    status = read_status(folder)
    assert status["status"] == "done" and "unreachable" in status["index_error"]


def test_local_videos_are_left_alone(tmp_path, s3):
    sync = make_sync(tmp_path, s3)
    write_status(sync.out_root / "local-video", status="queued", step="queued", video_path="/tmp/x.mp4")
    sync.tick()
    assert read_status(sync.out_root / "local-video")["step"] == "queued"


def test_abandoned_upload_fails_after_the_link_expires(tmp_path, s3):
    sync = make_sync(tmp_path, s3)
    folder = start_upload(sync)
    write_status(folder, created_at=time.time() - 2 * 3600)
    sync.tick()
    status = read_status(folder)
    assert status["status"] == "failed" and "upload didn't finish" in status["error"]


def test_job_that_ends_without_reporting_is_failed(tmp_path, s3):
    batch = FakeBatch()
    batch.jobs.append({"jobId": "job-3", "status": "SUCCEEDED", "createdAt": 5,
                       "parameters": {"input": f"s3://{BUCKET}/{KEY}"}})
    sync = make_sync(tmp_path, s3, batch=batch)
    folder = start_upload(sync, uploaded_at=time.time())
    write_status(folder, step="queued")
    sync.tick()
    status = read_status(folder)
    assert status["status"] == "failed" and "worker image" in status["error"]
