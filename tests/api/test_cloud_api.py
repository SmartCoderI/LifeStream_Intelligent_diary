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
