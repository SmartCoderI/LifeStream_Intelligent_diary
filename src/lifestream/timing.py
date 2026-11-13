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
# Every span maps to exactly one category. Order = display order = chart color order.
CATEGORIES = {
    "speech": "Speech models",
    "llm": "LLM",
    "model_loading": "Model loading",
    "media": "Media (FFmpeg)",
    "embedding": "Embedding",
    "database": "Database",
    "upload_queue": "Upload & queue",
    "other": "Other",
}
# dashboard step names (status.json) -> category, for the live "running" span
STEP_CATEGORIES = {
    "validating": "media", "extracting_audio": "media", "detecting_scenes": "media",
    "transcribing": "speech", "diarizing": "speech", "classifying": "llm", "summarizing": "llm",
    "indexing": "embedding", "queued": "upload_queue",
}


def category_of(span: dict) -> str:
    if span.get("category") in CATEGORIES:
        return span["category"]
    name, kind = span.get("name", ""), span.get("kind")
    if kind == "io":
        return "upload_queue"
    if kind == "load":
        return "model_loading"
    if kind == "llm":
        return "llm"
    if name.endswith(".db"):
        return "database"
    if name.endswith(".embed"):
        return "embedding"
    if name in ("probe", "extract_audio", "visual"):
        return "media"
    if name.split(".")[0] in ("transcribe", "diarize"):
        return "speech"
    return "other"


def _union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def breakdown(timeline: dict) -> list[dict]:
    """Wall-clock seconds per category; the categories add up to the run's total.

    - A step with sub-steps is split: each sub-step to its own category, the rest
      of the parent's time to the parent's category.
    - Work that ran in parallel (scene detection) only counts for the part nobody
      was waiting on anything else.
    - Gaps between recorded steps count as Other.
    """
    spans = [s for s in timeline.get("spans", []) if s.get("kind") != "cached"]
    total = float(timeline.get("total") or 0)
    secs = {c: 0.0 for c in CATEGORIES}
    top = [s for s in spans if not s.get("parent")]
    serial = [s for s in top if not (s.get("meta") or {}).get("parallel")]
    for s in serial:
        children = [c for c in spans if c.get("parent") == s["name"]]
        for c in children:
            secs[category_of(c)] += c["duration"]
        secs[category_of(s)] += max(0.0, s["duration"] - sum(c["duration"] for c in children))
    covered = _union([(s["start"], s["end"]) for s in serial])
    for s in top:
        if (s.get("meta") or {}).get("parallel"):
            overlap = sum(max(0.0, min(e, s["end"]) - max(b, s["start"])) for b, e in covered)
            secs[category_of(s)] += max(0.0, s["duration"] - overlap)
    secs["other"] += max(0.0, total - sum(secs.values()))
    return [{"category": c, "label": CATEGORIES[c], "seconds": round(v, 3),
             "share": round(v / total, 4) if total else 0.0} for c, v in secs.items() if v > 0.0005]


def _bottleneck(items: list[dict]) -> dict | None:
    real = [i for i in items if i["category"] != "other"]
    return max(real, key=lambda i: i["seconds"], default=None)


def run_stats(timeline: dict) -> dict:
    """Headline numbers for one run: totals per phase, LLM time and tokens, slowest step."""
    spans = timeline.get("spans", [])
    top = [s for s in spans if not s.get("parent")]
    io = {s["name"]: s["duration"] for s in top if s.get("kind") == "io"}
    work = [s for s in top if s.get("kind") != "io"]
    processing = (max(s["end"] for s in work) - min(s["start"] for s in work)) if work else 0.0
    llm = [s for s in spans if s.get("kind") == "llm"]
    slowest = max((s for s in work if s.get("kind") != "cached"), key=lambda s: s["duration"], default=None)
    video_duration = timeline.get("video_duration")
    return {
        "total": timeline.get("total"),
        "upload": io.get("upload"),
        "queue": io.get("queue"),
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


def aggregate(runs: list[dict], queries: list[dict]) -> dict:
    """Headline numbers across recent runs and queries.

    Only full runs count: finished, nothing reused (a re-run that skipped
    transcription would make speech look fast), and recorded with run settings
    (older records lack the database/embedding split)."""
    full = [r for r in runs if r.get("status") == "done" and not run_stats(r)["cached_steps"]
            and r.get("video_duration") and r.get("config")]
    secs: dict[str, float] = {}
    total_time = sum(float(r.get("total") or 0) for r in full)
    video_minutes = sum(r["video_duration"] for r in full) / 60
    for r in full:
        for item in breakdown(r):
            secs[item["category"]] = secs.get(item["category"], 0.0) + item["seconds"]
    items = [{"category": c, "label": CATEGORIES[c], "seconds": round(secs[c], 3),
              "share": round(secs[c] / total_time, 4) if total_time else 0.0,
              "per_video_minute": round(secs[c] / video_minutes, 2) if video_minutes else None}
             for c in CATEGORIES if secs.get(c, 0) > 0.0005]
    speeds = [run_stats(r)["speed"] for r in full if run_stats(r)["speed"] is not None]

    done_q = [q for q in queries if q.get("status") == "done"]
    totals = [q["total"] for q in done_q]

    def per_query(category: str) -> list[float]:
        return [sum(i["seconds"] for i in breakdown(q) if i["category"] == category) for q in done_q]

    return {
        "runs": len(full),
        "runs_skipped": len(runs) - len(full),
        "config": next((r.get("config") for r in runs if r.get("config")), None),
        "speed_median": _median(speeds),
        "total_median": _median([float(r.get("total") or 0) for r in full]),
        "breakdown": items,
        "bottleneck": _bottleneck(items),
        "llm_share": next((i["share"] for i in items if i["category"] == "llm"), 0.0),
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
