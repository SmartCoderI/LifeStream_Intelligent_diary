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
from lifestream.timing import aggregate, breakdown, budget_line_of, category_of  # noqa: E402


def sp(name, kind, start, end, parent=None, **meta):
    return {"name": name, "label": name, "kind": kind, "parent": parent, "start": start, "end": end,
            "duration": end - start, "meta": meta}


def as_dict(items):
    return {i["category"]: round(i["seconds"], 3) for i in items}


def test_categories():
    assert category_of(sp("upload", "io", 0, 1)) == "transfer"
    for name in ("s3_download", "s3_upload_results", "results_download"):
        assert category_of(sp(name, "io", 0, 1)) == "transfer"
    assert category_of(sp("gpu_start", "io", 0, 1)) == "cold_start"
    assert category_of(sp("gpu_start.pull", "io", 0, 1, "gpu_start")) == "cold_start"
    assert category_of(sp("queue", "io", 0, 1)) == "cold_start"             # this computer: waiting in the queue
    assert category_of(sp("transcribe.load_model", "load", 0, 1)) == "model_loading"
    assert category_of(sp("transcribe.asr", "step", 0, 1)) == "transcription"
    assert category_of(sp("diarize.run", "step", 0, 1)) == "speakers"
    assert category_of(sp("summarize", "llm", 0, 1)) == "llm"
    assert category_of(sp("index.db", "step", 0, 1)) == "database"
    assert category_of(sp("search.embed", "step", 0, 1)) == "embedding"
    assert category_of(sp("extract_audio", "step", 0, 1)) == "media"
    assert category_of(sp("visual", "step", 0, 1)) == "media"
    assert category_of(sp("render", "step", 0, 1)) == "other"
    assert category_of(sp("pickup", "step", 0, 1)) == "other"
    assert category_of(sp("step.diarizing", "step", 0, 1)) == "speakers"   # live view of finished cloud steps
    assert category_of({"name": "running.x", "kind": "running", "category": "transcription"}) == "transcription"


def test_budget_lines_include_model_loading():
    assert budget_line_of(sp("transcribe.load_model", "load", 0, 1, "transcribe")) == "whisperx"
    assert budget_line_of(sp("diarize", "step", 0, 1)) == "pyannote"
    assert budget_line_of(sp("index.embed", "step", 0, 1, "index")) == "summary"
    assert budget_line_of(sp("classify", "llm", 0, 1)) == "summary"
    assert budget_line_of(sp("gpu_start.wait", "io", 0, 1, "gpu_start")) == "cold_start"
    assert budget_line_of(sp("visual", "step", 0, 1)) == "other"


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
    assert d == {"transfer": 5, "model_loading": 10, "transcription": 40, "llm": 30, "other": 15}
    assert round(sum(d.values()), 6) == 100
    assert [i["category"] for i in breakdown(tl)] == ["transfer", "model_loading", "transcription", "llm", "other"]


def test_parallel_work_counts_only_when_nobody_else_is_working():
    tl = {"total": 60, "spans": [
        sp("transcribe", "step", 0, 40),
        sp("visual", "step", 10, 50, parallel=True),  # 30 s hidden behind transcription, 10 s exposed
        sp("summarize", "llm", 50, 60),
    ]}
    assert as_dict(breakdown(tl)) == {"transcription": 40, "media": 10, "llm": 10}
    assert run_stats(tl)["parallel"] == [{"label": "visual", "duration": 40, "hidden": 30}]


def test_whisperx_and_pyannote_run_one_after_the_other():
    """The real GPU run: scene detection (60 s) is hidden behind the audio; speech steps are sequential."""
    tl = {"total": 126.8, "spans": [
        sp("probe", "step", 0.3, 0.4), sp("extract_audio", "step", 0.4, 1.8),
        sp("transcribe", "step", 1.8, 85.1), sp("visual", "step", 2.1, 62.4, parallel=True),
        sp("diarize", "step", 85.1, 126.5)]}
    d = as_dict(breakdown(tl))
    assert d["transcription"] == 83.3 and d["speakers"] == 41.4 and d["media"] == 1.5  # no scene time counted
    assert run_stats(tl)["parallel"][0]["hidden"] == 60.3


def test_two_parallel_spans_past_the_serial_steps_count_once():
    """Diarization alongside transcription and scene detection both run on after transcription ends:
    the 40-50 s stretch where both were running counts once (for the one that started first)."""
    tl = {"total": 60, "spans": [
        sp("transcribe", "step", 0, 40),
        sp("visual", "step", 1, 50, parallel=True),    # 10 s exposed (40-50)
        sp("diarize", "step", 2, 55, parallel=True),   # only 50-55 left for it
        sp("diarize.run", "step", 3, 54, "diarize"),   # children of a parallel span aren't split out
        sp("summarize", "llm", 55, 60),
    ]}
    d = as_dict(breakdown(tl))
    assert d == {"transcription": 40, "media": 10, "speakers": 5, "llm": 5}
    assert round(sum(d.values()), 6) == 60
    assert run_stats(tl)["parallel"] == [{"label": "visual", "duration": 49, "hidden": 39},
                                         {"label": "diarize", "duration": 53, "hidden": 48}]


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
    assert a["environment"] == "local" and a["budget_lines"] is None
    assert a["speed_median"] == round(100 / 60, 2)
    assert a["bottleneck"]["category"] in ("transcription", "llm") and a["llm_share"] == 0.5
    transcription = next(i for i in a["breakdown"] if i["category"] == "transcription")
    assert transcription["per_video_minute"] == 50.0 and transcription["median"] == 50
    assert (a["queries"]["p50"], a["queries"]["p95"]) == (3, 10)
    assert a["queries"]["database_p50"] == 0.1
    assert a["config"] == {"device": "cpu"}


def test_aggregate_empty():
    a = aggregate([], [])
    assert a["runs"] == 0 and a["breakdown"] == [] and a["queries"]["p50"] is None


# ---------- Stage 3: cloud runs ----------

def cloud_run(total=1000, gpu_start=400, video=600):
    """upload 20 s, GPU start, download 2 s, work, upload results 3 s, server download 1 s, index 4 s."""
    t = 20 + gpu_start
    spans = [sp("upload", "io", 0, 20), sp("gpu_start", "io", 20, t), sp("gpu_start.pull", "io", 100, t, "gpu_start"),
             sp("s3_download", "io", t, t + 2), sp("extract_audio", "step", t + 2, t + 12),
             sp("transcribe", "step", t + 12, t + 302), sp("summarize", "llm", t + 302, total - 10),
             sp("s3_upload_results", "io", total - 10, total - 7), sp("results_download", "io", total - 6, total - 5),
             sp("index", "step", total - 4, total), sp("index.db", "step", total - 1, total, "index")]
    return {"status": "done", "total": total, "video_duration": video, "source": "cloud",
            "config": {"device": "cuda"}, "spans": spans}


def test_cloud_run_stats():
    stats = run_stats(cloud_run())
    assert stats["environment"] == "cloud" and stats["cold_start"] == 400
    # processing = work only, not upload / cold start / transfers
    assert stats["processing"] == 10 + 290 + (1000 - 10 - 722) + 4
    d = as_dict(stats["breakdown"])
    assert d["cold_start"] == 400 and d["transfer"] == 20 + 2 + 3 + 1 and d["media"] == 10
    assert d["llm"] == 1000 - 10 - 722 and d["embedding"] == 3 and d["database"] == 1
    assert round(sum(d.values()), 6) == 1000
    assert stats["budget"] is None  # a 10-minute video isn't checked against the 1-hour budget


def test_budget_check_for_a_one_hour_cloud_video():
    run = cloud_run(total=1100, gpu_start=420, video=3600)
    budget = run_stats(run)["budget"]
    rows = {r["line"]: r for r in budget["rows"]}
    assert rows["cold_start"]["seconds"] == 420 and rows["cold_start"]["within"] is False   # > 6 min
    assert rows["ffmpeg"]["within"] is True and rows["ffmpeg"]["target"] == "< 1 min"
    assert rows["summary"]["seconds"] == (1090 - 742) + 4                                   # Claude + indexing
    assert budget["total"] == 1100 and budget["passed"] is True                             # < 20 min


def test_aggregate_keeps_cloud_and_local_runs_apart():
    local = {"status": "done", "total": 100, "video_duration": 60, "config": {"device": "cpu"},
             "source": "dashboard", "spans": [sp("transcribe", "step", 0, 100)]}
    one_hour = cloud_run(total=1100, gpu_start=500, video=3600)
    runs = [one_hour, cloud_run(gpu_start=300), local]
    a = aggregate(runs, [])
    assert a["environment"] == "cloud" and a["environments"] == {"cloud": 2, "local": 1}
    assert a["runs"] == 2 and a["cold_start_median"] == 400 and a["config"] == {"device": "cuda"}
    lines = {b["line"]: b for b in a["budget_lines"]}
    assert list(lines) == ["cold_start", "ffmpeg", "whisperx", "pyannote", "summary"]
    assert lines["cold_start"]["median"] == 400 and lines["cold_start"]["target"] == "≤ 6 min"
    assert a["budget"]["video_duration"] == 3600  # the newest ~1-hour cloud run
    local_only = aggregate(runs, [], "local")
    assert local_only["runs"] == 1 and local_only["budget"] is None and local_only["budget_lines"] is None
    assert aggregate([local], [])["environment"] == "local"  # no cloud runs yet
