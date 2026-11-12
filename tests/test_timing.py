"""Latency recording: spans, live timeline file, stats, logs."""

import json

import pytest
import time

from lifestream.timing import Timings, append_log, read_log, run_stats


def test_steps_record_start_end_label_and_meta(tmp_path):
    t = Timings(tmp_path / "timeline.json", meta={"video_duration": 10})
    with t.step("classify", "Classify (Claude)", kind="llm") as span:
        time.sleep(0.02)
        span["meta"]["input_tokens"] = 100
    (s,) = t.spans
    assert s["label"] == "Classify (Claude)" and s["kind"] == "llm"
    assert s["end"] > s["start"] and s["duration"] >= 0.02
    assert s["meta"] == {"input_tokens": 100}
    assert t.steps["classify"] == pytest.approx(s["duration"], abs=0.006)


def test_timeline_file_written_at_start_and_after_every_span(tmp_path):
    path = tmp_path / "timeline.json"
    path.write_text('{"old": "previous run"}')
    t = Timings(path)
    assert json.loads(path.read_text())["status"] == "running"  # previous run replaced right away
    with t.step("a"):
        pass
    assert [s["name"] for s in json.loads(path.read_text())["spans"]] == ["a"]
    t.finish("failed", "boom")
    data = json.loads(path.read_text())
    assert (data["status"], data["error"]) == ("failed", "boom") and data["finished_at"]


def test_children_cached_and_extra_spans():
    now = time.time()
    t = Timings(extra_spans=[{"name": "upload", "label": "Upload", "kind": "io", "parent": None,
                              "start": now - 5, "end": now - 3, "duration": 2.0, "meta": {}}])
    with t.step("transcribe"):
        with t.step("transcribe.load_model", "Load", kind="load", parent="transcribe"):
            pass
    t.cached("diarize", "Diarize (reused)")
    names = [s["name"] for s in t.timeline()["spans"]]
    assert names[0] == "upload"  # sorted by start time
    assert set(names) == {"upload", "transcribe", "transcribe.load_model", "diarize"}
    assert "transcribe.load_model" not in t.steps  # children don't count twice
    assert t.timeline()["total"] >= 5


def test_run_stats():
    def span(name, kind, start, end, parent=None, meta=None):
        return {"name": name, "label": name.title(), "kind": kind, "parent": parent, "start": start,
                "end": end, "duration": end - start, "meta": meta or {}}
    tl = {"total": 100, "video_duration": 50, "spans": [
        span("upload", "io", 0, 4), span("queue", "io", 4, 10),
        span("transcribe", "step", 10, 60), span("transcribe.load_model", "load", 10, 20, "transcribe"),
        span("classify", "llm", 60, 63, meta={"input_tokens": 1000, "output_tokens": 50}),
        span("summarize", "llm", 63, 80, meta={"input_tokens": 3000, "output_tokens": 900}),
        span("diarize", "cached", 80, 80),
    ]}
    s = run_stats(tl)
    assert (s["upload"], s["queue"], s["processing"]) == (4, 6, 70)
    assert s["speed"] == 1.4  # 70 s of processing for a 50 s video
    assert (s["llm_seconds"], s["input_tokens"], s["output_tokens"]) == (20, 4000, 950)
    assert s["slowest"] == {"label": "Transcribe", "duration": 50}
    assert s["cached_steps"] == ["Diarize"]


def test_logs_newest_first_and_skip_bad_lines(tmp_path):
    path = tmp_path / "_logs" / "runs.jsonl"
    for i in range(3):
        append_log(path, {"n": i})
    with path.open("a") as f:
        f.write("not json\n")
    assert [r["n"] for r in read_log(path)] == [2, 1, 0]
    assert [r["n"] for r in read_log(path, limit=1)] == [2]
    assert read_log(tmp_path / "missing.jsonl") == []


# ---------------- breakdown + aggregate ----------------
from lifestream.timing import aggregate, breakdown, category_of  # noqa: E402


def sp(name, kind, start, end, parent=None, **meta):
    return {"name": name, "label": name, "kind": kind, "parent": parent, "start": start, "end": end,
            "duration": end - start, "meta": meta}


def as_dict(items):
    return {i["category"]: round(i["seconds"], 3) for i in items}


def test_categories():
    assert category_of(sp("upload", "io", 0, 1)) == "upload_queue"
    assert category_of(sp("transcribe.load_model", "load", 0, 1)) == "model_loading"
    assert category_of(sp("transcribe.asr", "step", 0, 1)) == "speech"
    assert category_of(sp("summarize", "llm", 0, 1)) == "llm"
    assert category_of(sp("index.db", "step", 0, 1)) == "database"
    assert category_of(sp("search.embed", "step", 0, 1)) == "embedding"
    assert category_of(sp("extract_audio", "step", 0, 1)) == "media"
    assert category_of(sp("render", "step", 0, 1)) == "other"
    assert category_of({"name": "running.x", "kind": "running", "category": "speech"}) == "speech"


def test_breakdown_splits_parents_and_adds_up_to_total():
    tl = {"total": 100, "spans": [
        sp("upload", "io", 0, 5),
        sp("transcribe", "step", 5, 55),
        sp("transcribe.load_model", "load", 5, 15, "transcribe"),
        sp("transcribe.asr", "step", 15, 50, "transcribe"),       # 5 s of the parent left over
        sp("summarize", "llm", 60, 90),                            # 55-60 and 90-100 are gaps
        sp("extract_audio", "cached", 55, 55),                     # reused: ignored
    ]}
    d = as_dict(breakdown(tl))
    assert d == {"upload_queue": 5, "model_loading": 10, "speech": 40, "llm": 30, "other": 15}
    assert round(sum(d.values()), 6) == 100


def test_parallel_work_counts_only_when_nobody_else_is_working():
    tl = {"total": 60, "spans": [
        sp("transcribe", "step", 0, 40),
        sp("visual", "step", 10, 50, parallel=True),  # 30 s hidden behind transcription, 10 s exposed
        sp("summarize", "llm", 50, 60),
    ]}
    assert as_dict(breakdown(tl)) == {"speech": 40, "llm": 10, "media": 10}


def test_aggregate_uses_full_runs_and_percentiles():
    full = {"status": "done", "total": 100, "video_duration": 60, "config": {"device": "cpu"}, "spans": [
        sp("transcribe.asr", "step", 0, 50, "transcribe"), sp("transcribe", "step", 0, 50),
        sp("summarize", "llm", 50, 100)]}
    reused = {**full, "spans": [sp("transcribe", "cached", 0, 0), sp("summarize", "llm", 0, 30)], "total": 30}
    failed = {**full, "status": "failed"}
    old_format = {k: v for k, v in full.items() if k != "config"}
    queries = [{"status": "done", "total": t, "spans": [sp("search.db", "step", 0, 0.1, "search"),
                                                        sp("search", "step", 0, 0.1), sp("answer", "llm", 0.1, t)]}
               for t in [1, 2, 3, 4, 10]]
    a = aggregate([full, reused, failed, old_format], queries)
    assert (a["runs"], a["runs_skipped"]) == (1, 3)
    assert a["speed_median"] == round(100 / 60, 2)
    assert a["bottleneck"]["category"] in ("speech", "llm") and a["llm_share"] == 0.5
    speech = next(i for i in a["breakdown"] if i["category"] == "speech")
    assert speech["per_video_minute"] == 50.0  # 50 s per minute of video
    assert (a["queries"]["p50"], a["queries"]["p95"]) == (3, 10)
    assert a["queries"]["database_p50"] == 0.1
    assert a["config"] == {"device": "cpu"}


def test_aggregate_empty():
    a = aggregate([], [])
    assert a["runs"] == 0 and a["breakdown"] == [] and a["queries"]["p50"] is None
