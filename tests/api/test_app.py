"""F12 (local): API request/response shapes, uploads, status, summaries, errors."""

import time
from functools import partial

import pytest
from fastapi.testclient import TestClient

from lifestream.api.app import create_app
from lifestream.config import Settings
from lifestream.pipeline import Components, process_video

from fakes import FakeLLM, fake_diarize, fake_transcribe, fake_visual

FAKES = Components(transcribe=fake_transcribe, diarize=fake_diarize, visual=fake_visual)


def failing_process(*args, **kwargs):
    raise RuntimeError("diarization model unavailable")


@pytest.fixture
def make_client(tmp_path):
    def build(process_fn=None):
        process_fn = process_fn or partial(process_video, llm=FakeLLM("meeting"), components=FAKES,
                                           parallel=False)
        app = create_app(out_root=tmp_path / "out", uploads_dir=tmp_path / "uploads",
                         settings=Settings(), process_fn=process_fn, web_dist=None)
        return TestClient(app)
    return build


def upload(client, path, date="2026-11-04", name=None):
    with open(path, "rb") as f:
        return client.post("/api/videos", data={"date": date},
                           files={"file": (name or path.name, f, "video/mp4")})


def wait_done(client, video_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        status = client.get(f"/api/videos/{video_id}/status").json()
        if status["status"] in ("done", "failed"):
            return status
        time.sleep(0.1)
    raise TimeoutError(status)


def test_upload_process_and_read_everything(make_client, format_samples):
    with make_client() as client:
        r = upload(client, format_samples["mp4"], name="Team Meeting.mp4")
        assert r.status_code == 202
        video = r.json()
        assert video["video_id"] == "team-meeting"
        assert video["status"] == "queued" and video["size_bytes"] > 0

        status = wait_done(client, "team-meeting")
        assert status == {"video_id": "team-meeting", "status": "done", "step": "done",
                          "step_label": "Completed", "progress": 100, "error": None}

        listed = client.get("/api/videos").json()
        assert [v["video_id"] for v in listed] == ["team-meeting"]
        assert listed[0]["title"] == "Frontend sync" and listed[0]["type"] == "meeting"

        detail = client.get("/api/videos/team-meeting").json()
        assert detail["summary"]["meeting"]["decisions"] == ["Ship the navigation bar on Friday"]
        assert [s["speaker"] for s in detail["script"]] == ["SPEAKER_00", "SPEAKER_01"]
        assert {s["speaker"] for s in detail["speakers"]} == {"SPEAKER_00", "SPEAKER_01"}
        assert detail["timings"]["total"] > 0

        summary = client.get("/api/videos/team-meeting/summary").json()
        assert summary["markdown"].startswith("# Frontend sync")

        media = client.get("/api/videos/team-meeting/media", headers={"Range": "bytes=0-99"})
        assert media.status_code == 206 and len(media.content) == 100  # seeking works

        assert client.get("/api/days").json() == [{"date": "2026-11-04", "video_count": 1}]
        day = client.get("/api/days/2026-11-04").json()
        assert day["markdown"].startswith("# Daily Summary: 2026-11-04")
        assert day["videos"][0]["summary"]["meeting"]["title"] == "Frontend sync"


def test_same_name_gets_new_id(make_client, format_samples):
    with make_client() as client:
        a = upload(client, format_samples["mp4"]).json()["video_id"]
        b = upload(client, format_samples["mp4"]).json()["video_id"]
        assert (a, b) == ("sample", "sample-2")
        wait_done(client, a), wait_done(client, b)


def test_failed_processing_reported(make_client, format_samples):
    with make_client(failing_process) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        status = wait_done(client, vid)
        assert status["status"] == "failed"
        assert status["error"] == "diarization model unavailable"
        assert client.get(f"/api/videos/{vid}/summary").status_code == 404


def test_retry_after_failure(make_client, format_samples, tmp_path):
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary failure")
        return process_video(*args, llm=FakeLLM(), components=FAKES, parallel=False, **kwargs)

    with make_client(flaky) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        assert wait_done(client, vid)["status"] == "failed"
        assert client.post(f"/api/videos/{vid}/retry").status_code == 202
        assert wait_done(client, vid)["status"] == "done"
        assert client.post("/api/videos/nope/retry").status_code == 404


def test_invalid_video_fails_with_clear_message(make_client, format_samples):
    with make_client() as client:
        vid = upload(client, format_samples["no_audio"]).json()["video_id"]
        assert "no audio stream" in wait_done(client, vid)["error"]


@pytest.mark.parametrize("name, date, code, message", [
    ("notes.txt", "2026-11-04", 400, "Unsupported file type"),
    ("clip.mp4", "11/04/2026", 400, "YYYY-MM-DD"),
])
def test_upload_rejects_bad_input(make_client, format_samples, name, date, code, message):
    with make_client() as client:
        r = upload(client, format_samples["mp4"], date=date, name=name)
        assert r.status_code == code and message in r.json()["detail"]


def test_upload_too_large(tmp_path, format_samples):
    app = create_app(out_root=tmp_path / "out", uploads_dir=tmp_path / "up", settings=Settings(),
                     process_fn=failing_process, web_dist=None, max_upload_bytes=100)
    with TestClient(app) as client:
        r = upload(client, format_samples["mp4"])
        assert r.status_code == 413
        assert list((tmp_path / "up").iterdir()) == []


def test_unknown_and_unsafe_ids(make_client):
    with make_client() as client:
        assert client.get("/api/videos/nope").status_code == 404
        assert client.get("/api/videos/..%2F..%2Fetc").status_code == 404
        assert client.get("/api/days/2026-01-01").status_code == 404


def test_delete_removes_video_and_upload(make_client, format_samples, tmp_path):
    with make_client() as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        assert client.delete(f"/api/videos/{vid}").status_code == 204
        assert client.get("/api/videos").json() == []
        assert list((tmp_path / "uploads").iterdir()) == []
        assert client.get("/api/days").json() == []
