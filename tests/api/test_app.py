"""F12 (local): API request/response shapes, uploads, status, summaries, errors."""

import time
from functools import partial

import pytest
from fastapi.testclient import TestClient

from lifestream.api.app import create_app
from lifestream.config import Settings
from lifestream.pipeline import Components, process_video

from fakes import FakeLLM, fake_diarize, fake_index, fake_transcribe, fake_visual

FAKES = Components(transcribe=fake_transcribe, diarize=fake_diarize, visual=fake_visual, index=fake_index)


def failing_process(*args, **kwargs):
    raise RuntimeError("diarization model unavailable")


@pytest.fixture
def make_client(tmp_path):
    def build(process_fn=None):
        process_fn = process_fn or partial(process_video, llm=FakeLLM("meeting"), components=FAKES,
                                           parallel=False)
        app = create_app(out_root=tmp_path / "out", uploads_dir=tmp_path / "uploads",
                         settings=Settings(), process_fn=process_fn, web_dist=None,
                         index_fn=fake_index, unindex_fn=lambda vid, settings: None)
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


# ---------------- Stage 2 endpoints ----------------
from lifestream.intelligence.llm import LLMError  # noqa: E402
from lifestream.memory.store import MemoryUnavailable  # noqa: E402


def stage2_client(tmp_path, answer_fn=None, index_fn=None, unindex_fn=None):
    process = partial(process_video, llm=FakeLLM("meeting"), components=FAKES, parallel=False)
    app = create_app(out_root=tmp_path / "out", uploads_dir=tmp_path / "uploads", settings=Settings(),
                     process_fn=process, web_dist=None,
                     answer_fn=answer_fn or (lambda q, t: {"question": q, "answer": "a", "found": True,
                                                        "citations": [], "date_from": None, "date_to": None,
                                                        "time_phrase": None}),
                     index_fn=index_fn or fake_index, unindex_fn=unindex_fn or (lambda v, s: None))
    return TestClient(app)


def test_query_returns_answer_with_thumbnails(tmp_path, format_samples):
    def answer(q, timings):
        return {"question": q, "answer": "Fix the logo.", "found": True, "date_from": "2026-11-02",
                "date_to": "2026-11-08", "time_phrase": "last week",
                "citations": [{"video_id": "sample", "title": "Frontend sync", "date": "2026-11-04",
                               "kind": "summary", "section": "Topic", "start": 5, "end": 9, "snippet": "s"}]}
    with stage2_client(tmp_path, answer_fn=answer) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        r = client.post("/api/query", json={"question": "what about the logo last week?"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer"] == "Fix the logo." and body["date_from"] == "2026-11-02"
        assert "thumbnail" in body["citations"][0]


@pytest.mark.parametrize("error, code", [
    (MemoryUnavailable("Can't reach the search database."), 503),
    (LLMError("ANTHROPIC_API_KEY is missing"), 502),
])
def test_query_errors(tmp_path, error, code):
    def fail(q, timings):
        raise error
    with stage2_client(tmp_path, answer_fn=fail) as client:
        r = client.post("/api/query", json={"question": "hi"})
        assert r.status_code == code and str(error) in r.json()["detail"]


def test_query_validation(tmp_path):
    with stage2_client(tmp_path) as client:
        assert client.post("/api/query", json={"question": "  "}).status_code == 400
        assert client.post("/api/query", json={"question": "x" * 1001}).status_code == 400


def test_name_speakers_updates_everything(tmp_path, format_samples):
    indexed = []
    with stage2_client(tmp_path, index_fn=lambda out, s: indexed.append(out.name) or 5) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        indexed.clear()

        r = client.put(f"/api/videos/{vid}/speakers", json={"names": {"SPEAKER_00": "Maya"}})
        assert r.status_code == 200 and r.json()["indexed"] is True
        assert indexed == [vid]  # re-indexed with the new name

        speakers = {s["label"]: s for s in client.get(f"/api/videos/{vid}/speakers").json()}
        assert speakers["SPEAKER_00"]["speaker"] == "Maya" and speakers["SPEAKER_01"]["name"] is None

        detail = client.get(f"/api/videos/{vid}").json()
        assert detail["script"][0]["speaker"] == "Maya"
        assert detail["summary"]["meeting"]["topics"][0]["points"][0]["speaker"] == "Maya"
        assert "**[00:00:00] Maya:**" in client.get(f"/api/videos/{vid}/summary").json()["markdown"]
        day = client.get("/api/days/2026-11-04").json()
        assert "Maya" in day["markdown"]
        assert day["videos"][0]["summary"]["meeting"]["attendees"][0] == "Maya"

        # clearing a name restores the label
        client.put(f"/api/videos/{vid}/speakers", json={"names": {"SPEAKER_00": ""}})
        assert client.get(f"/api/videos/{vid}").json()["script"][0]["speaker"] == "SPEAKER_00"


def test_name_speakers_rejects_unknown_labels(tmp_path, format_samples):
    with stage2_client(tmp_path) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        r = client.put(f"/api/videos/{vid}/speakers", json={"names": {"SPEAKER_09": "X"}})
        assert r.status_code == 400 and "SPEAKER_09" in r.json()["detail"]


def test_names_saved_even_if_search_is_down(tmp_path, format_samples):
    def down(out, s):
        raise MemoryUnavailable("Can't reach the search database.")
    with stage2_client(tmp_path, index_fn=down) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        r = client.put(f"/api/videos/{vid}/speakers", json={"names": {"SPEAKER_01": "Leo"}})
        assert r.status_code == 200
        assert r.json()["indexed"] is False and "search database" in r.json()["index_error"]


def test_delete_also_removes_from_search_and_registry(tmp_path, format_samples):
    removed = []
    with stage2_client(tmp_path, unindex_fn=lambda v, s: removed.append(v)) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        client.put(f"/api/videos/{vid}/speakers", json={"names": {"SPEAKER_00": "Maya"}})
        assert client.delete(f"/api/videos/{vid}").status_code == 204
        assert removed == [vid]
        from lifestream.memory.registry import SpeakerRegistry
        assert SpeakerRegistry(Settings().speakers_file).names_for(vid) == {}


# ---------------- latency ----------------
def test_timeline_includes_upload_queue_and_steps(tmp_path, format_samples):
    with stage2_client(tmp_path) as client:
        started_ms = (time.time() - 1.5) * 1000  # browser started sending 1.5 s ago
        with open(format_samples["mp4"], "rb") as f:
            vid = client.post("/api/videos", data={"date": "2026-11-04", "client_started_at": str(started_ms)},
                              files={"file": ("clip.mp4", f, "video/mp4")}).json()["video_id"]
        wait_done(client, vid)
        tl = client.get(f"/api/videos/{vid}/timeline").json()
        names = [s["name"] for s in tl["spans"]]
        assert names[:3] == ["upload", "save_upload", "queue"]
        upload = tl["spans"][0]
        assert upload["kind"] == "io" and 1.4 < upload["duration"] < 5
        assert {"probe", "transcribe", "classify", "summarize", "index"} <= set(names)
        assert tl["status"] == "done" and tl["source"] == "dashboard"
        assert tl["stats"]["upload"] == upload["duration"] and tl["stats"]["processing"] > 0
        assert tl["total"] >= upload["duration"]


def test_timeline_while_queued_and_processing(tmp_path, format_samples):
    import threading
    gate = threading.Event()

    def slow(*args, on_step=None, **kwargs):
        on_step("transcribing")
        gate.wait(5)
        return process_video(*args, llm=FakeLLM(), components=FAKES, parallel=False, on_step=on_step, **kwargs)

    with stage2_client(tmp_path) as client:
        client.app.state.jobs.process_fn = slow
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        for _ in range(50):
            if client.get(f"/api/videos/{vid}/status").json()["step"] == "transcribing":
                break
            time.sleep(0.05)
        tl = client.get(f"/api/videos/{vid}/timeline").json()
        running = [s for s in tl["spans"] if s.get("running")]
        assert running and running[0]["label"] == "Transcribing speech"
        gate.set()
        wait_done(client, vid)
        assert not [s for s in client.get(f"/api/videos/{vid}/timeline").json()["spans"] if s.get("running")]


def test_timeline_missing_for_old_videos(tmp_path):
    (tmp_path / "out" / "old").mkdir(parents=True)
    (tmp_path / "out" / "old" / "metadata.json").write_text('{"duration": 5}')
    (tmp_path / "out" / "old" / "summary.json").write_text("{}")
    with stage2_client(tmp_path) as client:
        r = client.get("/api/videos/old/timeline")
        assert r.status_code == 404 and "before latency tracking" in r.json()["detail"]


def test_run_history_and_query_log(tmp_path, format_samples):
    def answer(q, timings):
        with timings.step("search", "Search"):
            pass
        with timings.step("answer", "Write answer (Claude)", kind="llm") as span:
            span["meta"]["input_tokens"] = 500
        return {"question": q, "answer": "a", "found": True, "citations": [], "date_from": None,
                "date_to": None, "time_phrase": None}
    with stage2_client(tmp_path, answer_fn=answer) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        runs = client.get("/api/latency/runs").json()
        assert runs[0]["video_id"] == vid and runs[0]["status"] == "done"
        assert runs[0]["title"] == "Frontend sync" and runs[0]["stats"]["processing"] > 0

        body = client.post("/api/query", json={"question": "what happened?"}).json()
        assert [s["name"] for s in body["timing"]["spans"]] == ["search", "answer"]
        queries = client.get("/api/latency/queries").json()
        assert queries[0]["question"] == "what happened?" and queries[0]["status"] == "done"
        assert queries[0]["stats"]["input_tokens"] == 500

        client.delete(f"/api/videos/{vid}")
        assert client.get("/api/latency/runs").json()[0]["deleted"] is True  # history kept


def test_failed_query_is_logged(tmp_path):
    def fail(q, timings):
        raise MemoryUnavailable("Can't reach the search database.")
    with stage2_client(tmp_path, answer_fn=fail) as client:
        assert client.post("/api/query", json={"question": "hi"}).status_code == 503
        q = client.get("/api/latency/queries").json()[0]
        assert q["status"] == "failed" and "search database" in q["error"]


def test_latency_summary(tmp_path, format_samples):
    def answer(q, timings):
        with timings.step("answer", "Write answer (Claude)", kind="llm"):
            pass
        return {"question": q, "answer": "a", "found": True, "citations": [], "date_from": None,
                "date_to": None, "time_phrase": None}
    with stage2_client(tmp_path, answer_fn=answer) as client:
        vid = upload(client, format_samples["mp4"]).json()["video_id"]
        wait_done(client, vid)
        client.post("/api/query", json={"question": "q"})
        summary = client.get("/api/latency/summary").json()
        assert summary["runs"] == 1 and summary["speed_median"] > 0
        assert abs(sum(i["share"] for i in summary["breakdown"]) - 1) < 0.01
        assert summary["queries"]["count"] == 1 and summary["queries"]["p50"] is not None
        assert summary["config"]["whisper_model"]
        run = client.get("/api/latency/runs").json()[0]
        assert run["stats"]["bottleneck"] is not None and run["stats"]["breakdown"]
