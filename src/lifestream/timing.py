"""Latency recording.

Each run gets a timeline of spans: one per step, with wall-clock start and
end times (epoch seconds), a label, a kind (for colouring), an optional parent
(sub-steps such as "load model") and details such as LLM token counts.

- timeline.json in the video's folder is rewritten after every span, so a
  video still processing shows its progress so far.
- timings.json keeps the simple {step: seconds} form used by earlier tools.
- Finished runs are appended to <out>/_logs/runs.jsonl (run history).
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# kinds: io (upload, queue, saving), step (normal work), load (loading a model),
#        llm (a call to the LLM: Qwen or Claude), cached (reused a saved result)
SpanKind = str


class Timings:
    def __init__(
        self,
        timeline_path: Path | None = None,
        extra_spans: list[dict] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.steps: dict[str, float] = {}
        self.spans: list[dict] = [dict(s) for s in (extra_spans or [])]
        self.timeline_path = timeline_path
        self.meta = dict(meta or {})
        self.started_at = time.time()
        self.status = "running"
        self.error: str | None = None
        self.finished_at: float | None = None
        self._lock = threading.Lock()
        self.save_timeline()  # a new run replaces the previous run's timeline right away

    # ---------- recording ----------
    @contextmanager
    def step(self, name: str, label: str | None = None, *, kind: SpanKind = "step",
             parent: str | None = None):
        """Time a block. Yields the span dict so the block can add details to span['meta']."""
        span: dict[str, Any] = {"name": name, "label": label or name, "kind": kind, "parent": parent,
                                "start": time.time(), "meta": {}}
        t0 = time.perf_counter()
        try:
            yield span
        finally:
            seconds = time.perf_counter() - t0
            span["end"] = span["start"] + seconds
            span["duration"] = round(seconds, 3)
            self._add(span, seconds)

    def add(self, name: str, label: str, start: float, end: float, *, kind: SpanKind = "step",
            parent: str | None = None, meta: dict | None = None) -> None:
        """Record a span measured elsewhere (e.g. in a child process)."""
        self._add({"name": name, "label": label, "kind": kind, "parent": parent, "start": start,
                   "end": end, "duration": round(end - start, 3), "meta": meta or {}}, end - start)

    def cached(self, name: str, label: str) -> None:
        """A step skipped because its saved result was reused."""
        now = time.time()
        self._add({"name": name, "label": label, "kind": "cached", "parent": None, "start": now,
                   "end": now, "duration": 0.0, "meta": {"cached": True}}, None)

    def record(self, name: str, seconds: float) -> None:
        """Duration-only entry for timings.json (no timeline span)."""
        with self._lock:
            self.steps[name] = round(seconds, 2)

    def _add(self, span: dict, seconds: float | None) -> None:
        with self._lock:
            self.spans.append(span)
            if seconds is not None and span.get("parent") is None:
                self.steps[span["name"]] = round(seconds, 2)
        self.save_timeline()

    # ---------- saving ----------
    def finish(self, status: str = "done", error: str | None = None) -> dict:
        self.status, self.error, self.finished_at = status, error, time.time()
        return self.save_timeline()

    def timeline(self) -> dict:
        with self._lock:
            spans = sorted(self.spans, key=lambda s: s["start"])
        start = min([s["start"] for s in spans] + [self.started_at])
        end = self.finished_at or time.time()
        return {**self.meta, "status": self.status, "error": self.error,
                "started_at": start, "finished_at": self.finished_at,
                "total": round(end - start, 3), "spans": spans}

    def save_timeline(self) -> dict:
        data = self.timeline()
        if self.timeline_path is not None:
            tmp = self.timeline_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self.timeline_path)
        return data

    def save(self, path: Path, total: float | None = None) -> None:
        data = dict(self.steps)
        if total is not None:
            data["total"] = round(total, 2)
        path.write_text(json.dumps(data, indent=2))


# ---------- analysis: where did the time go ----------
#
# Every span maps to one kind of work (CATEGORIES); a run's categories add up to its wall-clock
# time. The pipeline runs mostly one step after another: FFmpeg -> WhisperX -> pyannote -> Claude
# -> indexing. Scene detection is the exception: it runs in a separate process alongside the
# audio steps, so it only counts for the part nobody was waiting on anything else (the rest is
# reported under "parallel"). Runs on the cloud are also checked against the Stage 3 time budget
# (BUDGET), whose lines group the same spans differently.

CATEGORIES = {
    "transfer": "Upload & transfers",     # browser upload; cloud: S3 <-> GPU worker <-> server
    "cold_start": "Cold start & queue",   # cloud: GPU machine + image; this computer: waiting in the queue
    "media": "FFmpeg & scenes",
    "model_loading": "Model loading",
    "transcription": "Transcription (WhisperX)",
    "speakers": "Speakers (pyannote)",
    "llm": "LLM (Claude)",
    "embedding": "Embedding",
    "database": "Database",
    "other": "Other steps & waits",       # building the script, Markdown, waiting for the server to notice
}
TRANSFER_SPANS = {"upload", "save_upload", "s3_download", "s3_upload_results", "results_download"}
# dashboard step names (status.json) -> category, for the live "running" span
STEP_CATEGORIES = {
    "uploading": "transfer", "queued": "cold_start", "validating": "media", "extracting_audio": "media",
    "transcribing": "transcription", "diarizing": "speakers", "detecting_scenes": "media", "merging": "other",
    "classifying": "llm", "summarizing": "llm", "uploading_results": "transfer", "indexing": "embedding",
}

# Stage 3 time budget for a 1-hour video on g4dn.xlarge: line -> (label, upper bound in s, as written)
BUDGET = {
    "cold_start": ("Cold start (instance + image)", 360, "≤ 6 min"),
    "ffmpeg": ("FFmpeg", 60, "< 1 min"),
    "whisperx": ("WhisperX large-v3", 480, "4–8 min"),
    "pyannote": ("pyannote (alongside scene detection)", 240, "2–4 min"),
    "summary": ("Classification + summary + embeddings", 120, "~2 min"),
}
TOTAL_TARGET = (1200, "< 20 min")
BUDGET_MIN_VIDEO_S = 45 * 60  # only a run of a long video (~1 hour) is checked against the budget


def environment_of(timeline: dict) -> str:
    """'cloud' for runs on the AWS GPU worker, 'local' for runs on this computer."""
    return "cloud" if timeline.get("source") in ("cloud", "batch") else "local"


def category_of(span: dict) -> str:
    if span.get("category") in CATEGORIES:
        return span["category"]
    name, kind = span.get("name", ""), span.get("kind")
    root = name.split(".")[0]
    if root == "gpu_start" or name == "queue":
        return "cold_start"
    if name in TRANSFER_SPANS:
        return "transfer"
    if name.startswith("step."):  # live view: finished cloud steps
        return STEP_CATEGORIES.get(name[5:], "other")
    if kind == "load":
        return "model_loading"
    if kind == "llm":
        return "llm"
    if name.endswith(".db"):
        return "database"
    if name.endswith(".embed") or name == "index":
        return "embedding"
    if name in ("probe", "extract_audio", "visual"):
        return "media"
    if root == "transcribe":
        return "transcription"
    if root == "diarize":
        return "speakers"
    return "other"


def budget_line_of(span: dict) -> str:
    """Which line of the time budget a span counts toward (model loading counts toward its step)."""
    name = span.get("name", "")
    root = name.split(".")[0]
    return {"gpu_start": "cold_start", "probe": "ffmpeg", "extract_audio": "ffmpeg",
            "transcribe": "whisperx", "diarize": "pyannote", "classify": "summary", "summarize": "summary",
            "index": "summary"}.get(root, "other")


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _split(timeline: dict, classify) -> dict[str, float]:
    """Wall-clock seconds per group; the groups add up to the total.

    - A step with sub-steps is split: each sub-step to its own group, the rest
      of the parent's time to the parent's group.
    - Work that ran in parallel (scene detection) only counts for the part nobody
      was waiting on anything else.
    - Time no step recorded goes to "other".
    """
    spans = [s for s in timeline.get("spans", []) if s.get("kind") != "cached"]
    total = float(timeline.get("total") or 0)
    secs: dict[str, float] = {}
    top = [s for s in spans if not s.get("parent")]
    serial = [s for s in top if not (s.get("meta") or {}).get("parallel")]

    def add(group: str, seconds: float) -> None:
        secs[group] = secs.get(group, 0.0) + seconds

    for s in serial:
        children = [c for c in spans if c.get("parent") == s["name"]]
        for c in children:
            add(classify(c), c["duration"])
        add(classify(s), max(0.0, s["duration"] - sum(c["duration"] for c in children)))
    for s in top:
        if (s.get("meta") or {}).get("parallel"):
            add(classify(s), _exposed(s, serial))
    add("other", max(0.0, total - sum(secs.values())))
    return secs


def _exposed(span: dict, serial: list[dict]) -> float:
    covered = _union([(s["start"], s["end"]) for s in serial])
    overlap = sum(max(0.0, min(e, span["end"]) - max(b, span["start"])) for b, e in covered)
    return max(0.0, span["duration"] - overlap)


def breakdown(timeline: dict) -> list[dict]:
    """Wall-clock seconds per kind of work, in display order; they add up to the run's total."""
    secs = _split(timeline, category_of)
    total = float(timeline.get("total") or 0)
    return [{"category": c, "label": CATEGORIES[c], "seconds": round(secs[c], 3),
             "share": round(secs[c] / total, 4) if total else 0.0}
            for c in CATEGORIES if secs.get(c, 0.0) > 0.0005]


def parallel_work(timeline: dict) -> list[dict]:
    """Steps that ran alongside others: their full duration and how much of it was hidden."""
    top = [s for s in timeline.get("spans", []) if not s.get("parent") and s.get("kind") != "cached"]
    serial = [s for s in top if not (s.get("meta") or {}).get("parallel")]
    out = []
    for s in top:
        if (s.get("meta") or {}).get("parallel"):
            exposed = _exposed(s, serial)
            out.append({"label": s["label"], "duration": round(s["duration"], 3),
                        "hidden": round(s["duration"] - exposed, 3)})
    return out


def budget_seconds(timeline: dict) -> dict[str, float]:
    return _split(timeline, budget_line_of)


def _bottleneck(items: list[dict]) -> dict | None:
    real = [i for i in items if i["category"] not in ("other", "transfer")]
    return max(real, key=lambda i: i["seconds"], default=None)


def budget_check(timeline: dict) -> dict | None:
    """A cloud run of a ~1-hour video against the Stage 3 time budget (M3a); None otherwise."""
    if environment_of(timeline) != "cloud" or (timeline.get("video_duration") or 0) < BUDGET_MIN_VIDEO_S:
        return None
    seconds = budget_seconds(timeline)
    rows = [{"line": line, "label": label, "seconds": round(seconds.get(line, 0.0), 1), "target": text,
             "within": seconds.get(line, 0.0) <= limit} for line, (label, limit, text) in BUDGET.items()]
    total = float(timeline.get("total") or 0)
    return {"rows": rows, "total": total, "total_target": TOTAL_TARGET[1], "passed": total < TOTAL_TARGET[0],
            "video_duration": timeline.get("video_duration")}


def run_stats(timeline: dict) -> dict:
    """Headline numbers for one run (or query): totals per phase, LLM time and tokens, slowest step."""
    spans = timeline.get("spans", [])
    top = [s for s in spans if not s.get("parent")]
    io = {s["name"]: s["duration"] for s in top if s.get("kind") == "io"}
    work = [s for s in top if s.get("kind") != "io"]
    # time something was being processed; waits and transfers between steps don't count
    processing = sum(e - b for b, e in _union([(s["start"], s["end"]) for s in work]))
    start_spans = [s for s in top if s.get("name") in ("gpu_start", "queue")]
    gpu = next((s for s in top if s.get("name") == "gpu_start"), None)
    llm = [s for s in spans if s.get("kind") == "llm"]
    slowest = max((s for s in work if s.get("kind") != "cached"), key=lambda s: s["duration"], default=None)
    video_duration = timeline.get("video_duration")
    return {
        "total": timeline.get("total"),
        "upload": io.get("upload"),
        "queue": io.get("queue"),
        "environment": environment_of(timeline),
        # waiting before processing could start: GPU machine + image (cloud) or the queue (this computer)
        "cold_start": round(sum(s["duration"] for s in start_spans), 3) if start_spans else None,
        "cold": (gpu.get("meta") or {}).get("cold") if gpu else None,
        "processing": round(processing, 3),
        "llm_seconds": round(sum(s["duration"] for s in llm), 3),
        "input_tokens": sum((s.get("meta") or {}).get("input_tokens", 0) for s in llm),
        "output_tokens": sum((s.get("meta") or {}).get("output_tokens", 0) for s in llm),
        "slowest": {"label": slowest["label"], "duration": slowest["duration"]} if slowest else None,
        "cached_steps": [s["label"] for s in spans if s.get("kind") == "cached"],
        # seconds of processing per second of video (1.0 = real time)
        "speed": round(processing / video_duration, 2) if video_duration else None,
        "breakdown": (items := breakdown(timeline)),
        "bottleneck": _bottleneck(items),
        "parallel": parallel_work(timeline),
        "budget": budget_check(timeline),
        "config": timeline.get("config"),
    }


def _median(values: list[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (honest with few samples: returns a real observation)."""
    values = sorted(values)
    if not values:
        return None
    return values[max(0, min(len(values) - 1, int(-(-p * len(values) // 1)) - 1))]


def aggregate(runs: list[dict], queries: list[dict], environment: str | None = None) -> dict:
    """Headline numbers across recent runs and queries.

    Runs on the cloud GPU and on this computer aren't comparable, so the run numbers cover one
    environment: `environment`, or by default the cloud if it has any runs.
    Only full runs count: finished, nothing reused (a re-run that skipped
    transcription would make speech look fast), and recorded with run settings
    (older records lack the database/embedding split)."""
    counts = {"cloud": 0, "local": 0}
    for r in runs:
        counts[environment_of(r)] += 1
    if environment not in counts:
        environment = "cloud" if counts["cloud"] else "local"
    runs = [r for r in runs if environment_of(r) == environment]
    full = [r for r in runs if r.get("status") == "done" and not run_stats(r)["cached_steps"]
            and r.get("video_duration") and r.get("config")]
    stats = [run_stats(r) for r in full]
    per_run = [{i["category"]: i["seconds"] for i in s["breakdown"]} for s in stats]
    total_time = sum(float(r.get("total") or 0) for r in full)
    video_minutes = sum(r["video_duration"] for r in full) / 60
    items = []
    for c, label in CATEGORIES.items():
        secs = sum(p.get(c, 0.0) for p in per_run)
        if secs > 0.0005:
            items.append({"category": c, "label": label, "seconds": round(secs, 3),
                          "share": round(secs / total_time, 4) if total_time else 0.0,
                          "per_video_minute": round(secs / video_minutes, 2) if video_minutes else None,
                          "median": _median([p.get(c, 0.0) for p in per_run])})
    cloud = environment == "cloud"
    budget_runs = [budget_seconds(r) for r in full] if cloud else []

    done_q = [q for q in queries if q.get("status") == "done"]
    totals = [q["total"] for q in done_q]

    def per_query(category: str) -> list[float]:
        return [sum(i["seconds"] for i in breakdown(q) if i["category"] == category) for q in done_q]

    cold = [s["cold_start"] for s in stats if s["cold_start"] is not None]
    return {
        "environment": environment,
        "environments": counts,
        "runs": len(full),
        "runs_skipped": len(runs) - len(full),
        "config": next((r.get("config") for r in runs if r.get("config")), None),
        "speed_median": _median([s["speed"] for s in stats if s["speed"] is not None]),
        "total_median": _median([float(r.get("total") or 0) for r in full]),
        "cold_start_median": _median(cold),
        "breakdown": items,
        "bottleneck": _bottleneck(items),
        "llm_share": round(sum(s["llm_seconds"] for s in stats) / total_time, 4) if total_time else 0.0,
        # cloud only: the time budget's lines with each line's median across runs, and the newest
        # ~1-hour run checked against it
        "budget_lines": [{"line": line, "label": label, "target": text,
                          "median": _median([b.get(line, 0.0) for b in budget_runs])}
                         for line, (label, _, text) in BUDGET.items()] if cloud else None,
        "budget": next((s["budget"] for s in stats if s["budget"]), None),
        "queries": {
            "count": len(done_q),
            "p50": _percentile(totals, 0.5),
            "p95": _percentile(totals, 0.95),
            "llm_p50": _percentile(per_query("llm"), 0.5),
            "database_p50": _percentile(per_query("database"), 0.5),
            "embedding_p50": _percentile(per_query("embedding"), 0.5),
            "breakdown": _sum_breakdowns(done_q),
        },
    }


def _sum_breakdowns(timelines: list[dict]) -> list[dict]:
    secs: dict[str, float] = {}
    total = sum(float(t.get("total") or 0) for t in timelines)
    for t in timelines:
        for item in breakdown(t):
            secs[item["category"]] = secs.get(item["category"], 0.0) + item["seconds"]
    return [{"category": c, "label": CATEGORIES[c], "seconds": round(secs[c], 3),
             "share": round(secs[c] / total, 4) if total else 0.0} for c in CATEGORIES if secs.get(c, 0) > 0.0005]


def sub_step(timings: "Timings | None", name: str, label: str, *, kind: str = "step", parent: str | None = None):
    """timings.step(...) or a no-op when timings is None."""
    from contextlib import nullcontext
    if timings is None:
        return nullcontext({"meta": {}})
    return timings.step(name, label, kind=kind, parent=parent)


def append_log(path: Path, record: dict) -> None:
    """Append one JSON line (run history, query log)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def read_log(path: Path, limit: int = 100) -> list[dict]:
    """Newest first. Unreadable lines are skipped."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[::-1][:limit]


def fmt_ts(seconds: float) -> str:
    """Seconds -> HH:MM:SS."""
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_ts(text: str) -> float | None:
    """'HH:MM:SS', 'MM:SS' or 'SS' -> seconds. Returns None if it can't be parsed."""
    try:
        parts = [float(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total
