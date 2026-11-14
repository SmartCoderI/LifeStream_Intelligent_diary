"""F12 (cloud mode): API key, presigned uploads, status/summary/day/query shapes, retry and delete.
S3 is mocked with moto, Batch with a fake, processing results come from the real pipeline with fakes."""

import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from lifestream.api.app import create_app
from lifestream.cloud.sync import CloudStore
from lifestream.config import Settings
from lifestream.pipeline import Components, process_video

from conftest import BUCKET
from fakes import FakeBatch, FakeLLM, fake_diarize, fake_index, fake_transcribe, fake_visual, upload_folder

KEY = "secret-key"
DATE = "2026-11-04"


def answer(question, timings):
    return {"question": question, "answer": "You agreed to ship on Friday [1].", "found": True,
            "citations": [{"video_id": "team-sync", "title": "Frontend sync", "date": DATE, "kind": "summary",
                           "section": "Decisions", "start": 12.0, "end": 30.0, "snippet": "Ship Friday"}],
            "date_from": None, "date_to": None, "time_phrase": None}


@pytest.fixture
def batch():
    return FakeBatch()


@pytest.fixture
def app(tmp_path, s3, batch):
    return create_app(out_root=tmp_path / "out", uploads_dir=tmp_path / "uploads",
                      settings=Settings(api_key=KEY), cloud=CloudStore(BUCKET, s3, batch),
                      start_worker=False, web_dist=None, answer_fn=answer, index_fn=fake_index,
                      unindex_fn=lambda vid, settings: None)


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-API-Key": KEY}) as c:
        yield c


def new_upload(client, filename="Team Sync.mp4", date=DATE, size=1234):
    return client.post("/api/videos", json={"date": date, "filename": filename, "size_bytes": size})


def finish_processing(app, s3, tmp_path, format_samples, video_id="team-sync"):
    """Simulate the browser's PUT and a successful GPU job, then let the server sync."""
    s3.put_object(Bucket=BUCKET, Key=f"uploads/{video_id}/{video_id}.mp4", Body=b"video", Metadata={"date": DATE})
    components = Components(transcribe=fake_transcribe, diarize=fake_diarize, visual=fake_visual,
                            index=lambda out, s: None)
    results = process_video(format_samples["mp4"], DATE, tmp_path / "worker", Settings(), llm=FakeLLM("meeting"),
                            components=components, parallel=False, video_id=video_id, log=lambda m: None)
    upload_folder(s3, BUCKET, results, f"out/{video_id}/")
    s3.put_object(Bucket=BUCKET, Key=f"out/{video_id}/status.json", Body=json.dumps(
        {"status": "done", "step": "done", "started_at": time.time(),
         "step_timings": {"transcribing": 1.0, "diarizing": 0.5}}).encode())
    app.state.jobs.tick()


# ---------- API key ----------

@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
@pytest.mark.parametrize("method,path", [("GET", "/api/videos"), ("POST", "/api/videos"),
                                         ("GET", "/api/videos/x/status"), ("GET", "/api/days/2026-11-04/summary"),
                                         ("POST", "/api/query")])
def test_missing_or_wrong_key_is_401(app, headers, method, path):
    with TestClient(app) as anonymous:
        r = anonymous.request(method, path, headers=headers, json={})
    assert r.status_code == 401 and r.json() == {"detail": "Missing or wrong API key"}


def test_key_as_cookie_and_open_config(app):
    with TestClient(app) as browser:
        assert browser.get("/api/config").json() == {"mode": "cloud", "auth": True}  # no key needed
        browser.cookies.set("lifestream_key", KEY)
        assert browser.get("/api/videos").status_code == 200  # how <img>/<video> requests authenticate


def test_local_mode_needs_no_key(tmp_path):
    app = create_app(out_root=tmp_path / "out", uploads_dir=tmp_path / "uploads", settings=Settings(),
                     start_worker=False, web_dist=None)
    with TestClient(app) as c:
        assert c.get("/api/config").json() == {"mode": "local", "auth": False}
        assert c.get("/api/videos").status_code == 200


# ---------- upload_video ----------

def test_upload_returns_a_presigned_put_url(client):
    r = new_upload(client)
    assert r.status_code == 201
    body = r.json()
    assert body["video_id"] == "team-sync" and body["status"] == "queued" and body["step"] == "uploading"
    assert body["upload_headers"] == {"x-amz-meta-date": DATE}

    url = urlparse(body["presigned_put_url"])
    assert url.scheme == "https" and BUCKET in url.netloc
    assert url.path == "/uploads/team-sync/team-sync.mp4"
    query = parse_qs(url.query)
    assert "X-Amz-Signature" in query and "x-amz-meta-date" in query["X-Amz-SignedHeaders"][0]
    assert int(query["X-Amz-Expires"][0]) == 3600


BIG = 40 * 1024**2  # 3 parts of 16 MiB


def test_large_upload_goes_up_in_parts_and_starts_when_put_together(client, s3):
    r = new_upload(client, size=BIG)
    assert r.json()["multipart"] is None  # only when the client asks for it
    client.delete(f"/api/videos/{r.json()['video_id']}")

    body = client.post("/api/videos", json={"date": DATE, "filename": "Team Sync.mp4", "size_bytes": BIG,
                                            "multipart": True}).json()
    mp = body["multipart"]
    assert body["presigned_put_url"] and mp["part_size"] == 16 * 1024**2 and len(mp["part_urls"]) == 3
    url = urlparse(mp["part_urls"][1])
    assert url.path == "/uploads/team-sync/team-sync.mp4"
    assert parse_qs(url.query)["partNumber"] == ["2"] and parse_qs(url.query)["uploadId"] == [mp["upload_id"]]

    key = "uploads/team-sync/team-sync.mp4"
    sizes = [mp["part_size"], mp["part_size"], BIG - 2 * mp["part_size"]]
    parts = [{"part_number": n, "etag": s3.upload_part(Bucket=BUCKET, Key=key, UploadId=mp["upload_id"],
                                                       PartNumber=n, Body=b"x" * size)["ETag"]}
             for n, size in enumerate(sizes, start=1)]
    assert s3.list_objects_v2(Bucket=BUCKET).get("KeyCount") == 0  # nothing until put together
    r = client.post("/api/videos/team-sync/uploaded", json={"parts": parts[::-1]})  # any order
    assert r.status_code == 202 and r.json()["step"] == "queued"
    head = s3.head_object(Bucket=BUCKET, Key=key)
    assert head["ContentLength"] == BIG and head["Metadata"] == {"date": DATE}  # the worker reads the date


def test_parts_that_dont_fit_together_are_a_400(client, s3):
    mp = client.post("/api/videos", json={"date": DATE, "filename": "a.mp4", "size_bytes": BIG,
                                          "multipart": True}).json()["multipart"]
    r = client.post("/api/videos/a/uploaded", json={"parts": [{"part_number": 1, "etag": '"nope"'}]})
    assert r.status_code == 400
    assert client.get("/api/videos/a/status").json()["step"] == "uploading"
    assert mp["upload_id"]


def test_cancel_or_delete_discards_a_part_upload(client, s3):
    for action in ("cancel", "delete"):
        vid = client.post("/api/videos", json={"date": DATE, "filename": f"{action}.mp4", "size_bytes": BIG,
                                               "multipart": True}).json()["video_id"]
        s3.upload_part(Bucket=BUCKET, Key=f"uploads/{vid}/{vid}.mp4",
                       UploadId=s3.list_multipart_uploads(Bucket=BUCKET)["Uploads"][0]["UploadId"],
                       PartNumber=1, Body=b"x" * 1024)
        if action == "cancel":
            assert client.post(f"/api/videos/{vid}/cancel").status_code == 202
        else:
            assert client.delete(f"/api/videos/{vid}").status_code == 204
        assert not s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads")


def test_same_name_gets_a_new_id(client):
    assert new_upload(client).json()["video_id"] == "team-sync"
    assert new_upload(client).json()["video_id"] == "team-sync-2"


@pytest.mark.parametrize("payload,code", [
    ({"date": "11/04/2026", "filename": "a.mp4"}, 400),
    ({"date": DATE, "filename": "notes.txt"}, 400),
    ({"date": DATE, "filename": "a.mp4", "size_bytes": 6 * 1024**3}, 413),
    ({"date": DATE, "filename": "a.mp4", "size_bytes": 0}, 400),
    ({"filename": "a.mp4"}, 422),
])
def test_upload_rejects_bad_requests(client, payload, code):
    assert client.post("/api/videos", json=payload).status_code == code


# ---------- get_status ----------

def test_status_schema_while_waiting(client):
    new_upload(client)
    assert client.post("/api/videos/team-sync/uploaded").status_code == 202
    status = client.get("/api/videos/team-sync/status").json()
    assert status == {"video_id": "team-sync", "status": "queued", "step": "queued",
                      "step_label": "Waiting in queue", "progress": 0, "error": None, "type": None,
                      "step_timings": {}}
    assert client.get("/api/videos/nope/status").status_code == 404


# ---------- the whole flow, then get_summary / days / media ----------

def test_upload_to_done_then_read_everything(app, client, s3, tmp_path, format_samples):
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    finish_processing(app, s3, tmp_path, format_samples)

    status = client.get("/api/videos/team-sync/status").json()
    assert status["status"] == "done" and status["type"] == "meeting"
    assert set(status["step_timings"]) == {"transcribing", "diarizing", "indexing"}

    summary = client.get("/api/videos/team-sync/summary").json()
    assert summary["summary"]["meeting"]["decisions"] == ["Ship the navigation bar on Friday"]
    assert summary["markdown"].startswith("#")

    day = client.get(f"/api/days/{DATE}/summary").json()
    assert day["date"] == DATE and [v["video_id"] for v in day["videos"]] == ["team-sync"]
    assert day["videos"][0]["summary"]["classification"]["type"] == "meeting" and day["markdown"]

    media = client.get("/api/videos/team-sync/media", follow_redirects=False)
    assert media.status_code == 307
    target = urlparse(media.headers["location"])
    assert target.path == "/uploads/team-sync/team-sync.mp4" and "X-Amz-Signature" in target.query


def test_query_memory_shape(client):
    r = client.post("/api/query", json={"question": "What did we decide last week?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("You agreed") and body["found"] is True
    (citation,) = body["citations"]
    assert {"video_id", "start", "end", "snippet"} <= set(citation)
    assert client.post("/api/query", json={"question": "  "}).status_code == 400


# ---------- retry and delete ----------

def test_retry_submits_a_batch_job(app, client, s3, batch):
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    s3.put_object(Bucket=BUCKET, Key="out/team-sync/status.json",
                  Body=json.dumps({"status": "failed", "step": "failed", "error": "boom"}).encode())
    app.state.jobs.tick()
    assert client.get("/api/videos/team-sync/status").json()["status"] == "failed"

    assert client.post("/api/videos/team-sync/retry").status_code == 202
    (job,) = batch.submitted
    assert job["jobQueue"] == "lifestream-gpu" and job["jobDefinition"] == "lifestream-worker"
    assert job["parameters"] == {"input": f"s3://{BUCKET}/uploads/team-sync/team-sync.mp4", "date": DATE}
    assert client.get("/api/videos/team-sync/status").json()["status"] == "queued"
    # the old failure was cleared, so the next sync doesn't report it again
    app.state.jobs.tick()
    assert client.get("/api/videos/team-sync/status").json()["status"] == "queued"


def test_delete_removes_the_s3_files(app, client, s3, tmp_path, format_samples):
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    finish_processing(app, s3, tmp_path, format_samples)
    assert client.delete("/api/videos/team-sync").status_code == 204
    assert s3.list_objects_v2(Bucket=BUCKET).get("KeyCount") == 0
    assert client.get("/api/videos/team-sync/status").status_code == 404


def test_unfinished_upload_can_be_deleted(client):
    new_upload(client)
    assert client.delete("/api/videos/team-sync").status_code == 204


# ---------- latency of cloud uploads ----------

def test_live_timing_while_uploading_and_waiting_for_a_gpu(client):
    new_upload(client)
    timeline = client.get("/api/videos/team-sync/timeline").json()
    (upload,) = timeline["spans"]
    assert upload["name"] == "upload" and upload["running"] and upload["category"] == "transfer"
    client.post("/api/videos/team-sync/uploaded")
    timeline = client.get("/api/videos/team-sync/timeline").json()
    assert [(s["name"], s["category"], bool(s.get("running"))) for s in timeline["spans"]] == [
        ("upload", "transfer", False), ("gpu_start", "cold_start", True)]


def test_a_cloud_upload_is_recorded_on_the_latency_page(app, client, s3, tmp_path, format_samples):
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    finish_processing(app, s3, tmp_path, format_samples)

    (run,) = client.get("/api/latency/runs").json()
    assert run["video_id"] == "team-sync" and run["status"] == "done" and run["source"] == "cloud"
    stages = {s["name"]: s["category"] for s in run["spans"] if not s["parent"]}
    assert stages["upload"] == "transfer" and stages["gpu_start"] == "cold_start"
    assert stages["results_download"] == "transfer" and stages["index"] == "embedding"
    assert run["stats"]["environment"] == "cloud" and run["stats"]["cold_start"] is not None

    summary = client.get("/api/latency/summary").json()
    assert summary["environment"] == "cloud" and summary["environments"] == {"cloud": 1, "local": 0}
    assert summary["runs"] == 1 and summary["cold_start_median"] is not None
    assert [t["target"] for t in summary["budget_lines"]] == ["≤ 6 min", "< 1 min", "4–8 min", "2–4 min", "~2 min"]

    timeline = client.get("/api/videos/team-sync/timeline").json()  # the video page's Timing tab
    assert timeline["status"] == "done" and timeline["stats"]["environment"] == "cloud"


# ---------- cancel ----------

def _job(batch, status="RUNNABLE", job_id="job-7"):
    batch.jobs.append({"jobId": job_id, "status": status, "createdAt": 5,
                       "parameters": {"input": f"s3://{BUCKET}/uploads/team-sync/team-sync.mp4"}})


def test_cancel_during_upload_deletes_the_file_and_stops_a_late_job(app, client, s3, batch):
    new_upload(client)
    s3.put_object(Bucket=BUCKET, Key="uploads/team-sync/team-sync.mp4", Body=b"video")  # the PUT just landed
    r = client.post("/api/videos/team-sync/cancel")
    assert r.status_code == 202 and r.json()["status"] == "cancelled" and r.json()["step_label"] == "Cancelled"
    assert s3.list_objects_v2(Bucket=BUCKET).get("KeyCount") == 0
    _job(batch)                      # EventBridge had already started a job
    app.state.jobs.tick()
    assert batch.terminated == ["job-7"]
    app.state.jobs.tick()            # stopped once, not again
    assert batch.terminated == ["job-7"]


def test_cancel_while_waiting_for_a_gpu_terminates_the_job(app, client, batch):
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    _job(batch)
    assert client.post("/api/videos/team-sync/cancel").status_code == 202
    assert batch.terminated == ["job-7"]
    assert client.get("/api/videos/team-sync/status").json()["status"] == "cancelled"


def test_cancel_while_processing_terminates_the_job_and_stays_cancelled(app, client, s3, batch):
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    _job(batch, status="RUNNING")
    s3.put_object(Bucket=BUCKET, Key="out/team-sync/status.json", Body=json.dumps(
        {"status": "processing", "step": "transcribing", "progress": 10, "job_id": "job-7"}).encode())
    app.state.jobs.tick()
    assert client.get("/api/videos/team-sync/status").json()["step"] == "transcribing"
    assert client.post("/api/videos/team-sync/cancel").status_code == 202
    assert batch.terminated == ["job-7"]
    app.state.jobs.tick()  # the worker's last status still says "processing": it must not come back
    assert client.get("/api/videos/team-sync/status").json()["status"] == "cancelled"


def test_cancel_is_refused_when_nothing_is_running(app, client, s3, tmp_path, format_samples):
    assert client.post("/api/videos/nope/cancel").status_code == 404
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    finish_processing(app, s3, tmp_path, format_samples)
    r = client.post("/api/videos/team-sync/cancel")
    assert r.status_code == 409 and "isn't being processed" in r.json()["detail"]


def test_cancelled_video_can_be_retried_or_deleted(app, client, s3, batch):
    new_upload(client)
    client.post("/api/videos/team-sync/uploaded")
    client.post("/api/videos/team-sync/cancel")
    assert client.post("/api/videos/team-sync/retry").status_code == 202  # the upload had finished
    assert batch.submitted[-1]["parameters"]["input"].endswith("uploads/team-sync/team-sync.mp4")
    new_upload(client, filename="Other.mp4")
    client.post("/api/videos/other/cancel")                               # cancelled mid-upload
    assert client.post("/api/videos/other/retry").status_code == 409      # nothing to process
    assert client.delete("/api/videos/other").status_code == 204
