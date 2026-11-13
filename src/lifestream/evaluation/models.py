"""Compare Claude models on the summary step (cost vs quality).

For each processed video, re-runs classification + summary with each model on the same saved
transcript and keyframes, with the same prompts and effort the pipeline uses. Saves every summary
for a side-by-side read, checks the M3b rules, and records tokens, cost and time.
Nothing in data/out is changed.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import yaml
from pydantic import TypeAdapter

from ..config import Settings
from ..intelligence import summarize
from ..intelligence.llm import LLMClient, cost_usd
from ..schemas import Keyframe, ScriptSegment, VideoSummary


@dataclass
class ModelRun:
    model: str
    video_id: str
    expected: str | None
    actual: str
    problems: list[str]
    points: int
    action_items: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    seconds: float
    saved_to: str
    calls: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (self.expected is None or self.expected == self.actual) and not self.problems


def labelled_videos(labels_dir: Path, out_root: Path) -> dict[str, str]:
    """video_id -> expected type, for labels whose video has been processed (script.json exists)."""
    found = {}
    for path in sorted(labels_dir.glob("*.yaml")):
        label = yaml.safe_load(path.read_text()) or {}
        vid, kind = label.get("video_id"), label.get("type")
        if vid and kind and (out_root / vid / "script.json").exists():
            found[vid] = kind
    return found


def parse_spec(spec: str) -> tuple[str, str | None]:
    """'claude-opus-5-5@low' -> ('claude-opus-5-5', 'low'); no '@' = the pipeline's own effort."""
    model, _, effort = spec.partition("@")
    return model, effort or None


def run_model(model: str, llm: LLMClient, video_dir: Path, expected: str | None, save_dir: Path,
              settings: Settings, effort: str | None = None, label: str | None = None) -> ModelRun:
    segments = TypeAdapter(list[ScriptSegment]).validate_json((video_dir / "script.json").read_text())
    frames_path = video_dir / "keyframes.json"
    keyframes = (TypeAdapter(list[Keyframe]).validate_json(frames_path.read_text())
                 if frames_path.exists() else [])
    meta = json.loads((video_dir / "metadata.json").read_text())
    duration = float(meta["duration"])

    calls: list[dict] = []

    def usage(step: str) -> None:
        u = dict(getattr(llm, "last_usage", None) or {})
        calls.append({"step": step, "input_tokens": u.get("input_tokens", 0),
                      "output_tokens": u.get("output_tokens", 0), "model": u.get("model", model)})

    t0 = time.perf_counter()
    classification = summarize.classify(llm, segments, keyframes, video_dir, duration,
                                        window_s=settings.classify_window_s)
    usage("classify")
    body = summarize.summarize(llm, classification.type, segments, keyframes, video_dir, duration,
                               effort=effort or settings.llm_effort)
    usage("summarize")
    seconds = time.perf_counter() - t0

    video = VideoSummary(
        video_id=meta.get("video_id", video_dir.name), date=meta.get("date", "1970-01-01"),
        duration=duration, classification=classification,
        vlog=body if classification.type == "vlog" else None,
        meeting=body if classification.type == "meeting" else None,
    )
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / "summary.json").write_text(video.model_dump_json(indent=2))
    (save_dir / "summary.md").write_text(summarize.render_summary(video))

    problems: list[str] = []
    points = actions = 0
    if video.meeting is not None:
        problems = summarize.meeting_problems(video.meeting, duration)
        points = sum(len(t.points) for t in video.meeting.topics)
        actions = len(video.meeting.action_items)
    elif classification.type == "meeting":
        problems = ["classified as meeting but has no meeting summary"]

    tokens_in = sum(c["input_tokens"] for c in calls)
    tokens_out = sum(c["output_tokens"] for c in calls)
    return ModelRun(model=label or model, video_id=video.video_id, expected=expected, actual=classification.type,
                    problems=problems, points=points, action_items=actions, input_tokens=tokens_in,
                    output_tokens=tokens_out, cost_usd=cost_usd(model, tokens_in, tokens_out),
                    seconds=round(seconds, 1), saved_to=str(save_dir), calls=calls)


def compare_models(models: list[str], videos: dict[str, str | None], out_root: Path, save_root: Path,
                   settings: Settings, make_llm: Callable[[str], LLMClient],
                   log: Callable[[str], None] = print) -> list[ModelRun]:
    runs = []
    for vid, expected in videos.items():
        for spec in models:
            model, effort = parse_spec(spec)
            log(f"{vid}: {spec} ...")
            run = run_model(model, make_llm(model), out_root / vid, expected, save_root / spec / vid, settings,
                            effort=effort, label=spec)
            runs.append(run)
    (save_root / "results.json").write_text(json.dumps(
        [asdict(r) | {"passed": r.passed} for r in runs], indent=2))
    return runs
