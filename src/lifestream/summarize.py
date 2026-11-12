"""F6: classify each video (meeting | vlog) and write a structured summary with an LLM.

The LLM returns JSON matching a Pydantic schema; Markdown is rendered from that
JSON with templates, so the output format is always the same.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import ValidationError

from .llm import LLMClient
from .schemas import (
    Classification,
    Keyframe,
    MeetingSummary,
    ScriptSegment,
    VideoSummary,
    VlogSummary,
)
from .timing import fmt_ts, parse_ts

_env = Environment(
    loader=PackageLoader("lifestream", "templates"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_env.filters["ts"] = fmt_ts
_env.filters["cell"] = lambda text: str(text).replace("|", "\\|").replace("\n", " ")

CLASSIFY_SYSTEM = """\
You classify recordings for a personal video diary into exactly one type.

meeting: two or more people talking with each other: meetings, interviews, \
calls, group discussions, classes with discussion.
vlog: mainly one person narrating, presenting or recording their own day: \
vlogs, body-cam footage, walk-and-talk, news-style reports, monologues.

You get the number of distinct speakers found by acoustic speaker diarization \
(reliable but may over-count by one when there is music or a short clip of \
another voice), the opening of the transcript, and a few frames."""

VLOG_SYSTEM = """\
You write a diary entry for a vlog-style video from its timestamped \
transcript and keyframes.

- title: a short descriptive title.
- overview: 2-3 sentences summarizing the whole video.
- location: where it was recorded. Use what the transcript or frames show. \
If you are guessing from visual or spoken clues, use source "inferred". If \
there is no clue, use value "Unknown" and source "unknown".
- activities: what happened, in order, each with its start time (HH:MM:SS, \
taken from the transcript timestamps). Cover the whole video.
- action_items: tasks, plans or follow-ups the speaker commits to or suggests \
to themselves. Empty list if there are none; do not invent any."""

MEETING_SYSTEM = """\
You write detailed minutes for a meeting or conversation from its timestamped \
transcript and keyframes.

- title: a short descriptive title.
- overview: 2-3 sentences summarizing the whole meeting and its outcome.
- location: where it took place, from transcript or frame clues ("inferred"), \
otherwise value "Unknown" and source "unknown".
- attendees: every speaker label in the transcript (e.g. SPEAKER_00). If the \
transcript reveals a speaker's name, write it as "SPEAKER_00 (Name)".
- topics: every topic discussed, in order. For each topic, list the \
substantive points in order. Each point has the speaker label exactly as in \
the transcript, the timestamp (HH:MM:SS) where it was said, and what was said \
in one or two specific sentences (numbers, names and reasons, not vague \
paraphrase). Aim for at least one point per minute of substantive discussion.
- decisions: what the group agreed on, one sentence each. Empty list if \
nothing was decided; do not invent any.
- action_items: concrete commitments or tasks, each with the owner's speaker \
label (or "Unassigned"), the timestamp where it was agreed, and a due date \
only if one was stated. Empty list if there are none; do not invent any."""


# ---------- helpers ----------
def format_transcript(segments: list[ScriptSegment], until: float | None = None) -> str:
    lines = []
    for s in segments:
        if until is not None and s.start > until:
            break
        lines.append(f"[{fmt_ts(s.start)}] {s.speaker}: {s.text}")
    return "\n".join(lines)


def count_speakers(segments: list[ScriptSegment], min_share: float = 0.02, min_seconds: float = 10.0) -> int:
    """Speakers with a meaningful amount of speech (ignores tiny spurious labels)."""
    talk: dict[str, float] = {}
    for s in segments:
        talk[s.speaker] = talk.get(s.speaker, 0.0) + (s.end - s.start)
    total = sum(talk.values()) or 1.0
    return sum(1 for v in talk.values() if v >= min_seconds or v / total >= min_share)


def _evenly(items: list, n: int) -> list:
    if len(items) <= n:
        return list(items)
    if n <= 1:
        return items[:n]
    stride = (len(items) - 1) / (n - 1)
    return [items[round(i * stride)] for i in range(n)]


def _image_blocks(keyframes: list[Keyframe], base_dir: Path) -> list[dict]:
    blocks: list[dict] = []
    for kf in keyframes:
        path = base_dir / kf.path
        if not path.exists():
            continue
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        blocks.append({"type": "text", "text": f"Keyframe at {fmt_ts(kf.time)}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    return blocks


# ---------- LLM steps ----------
def classify(
    llm: LLMClient,
    segments: list[ScriptSegment],
    keyframes: list[Keyframe],
    base_dir: Path,
    duration: float,
    window_s: float = 300.0,
    effort: str = "low",
) -> Classification:
    header = (
        f"Video length: {fmt_ts(duration)}\n"
        f"Distinct speakers (acoustic diarization): {count_speakers(segments)}\n\n"
        f"Transcript (first {int(window_s // 60)} minutes):\n"
        f"{format_transcript(segments, until=window_s) or '(no speech)'}"
    )
    content = [{"type": "text", "text": header}, *_image_blocks(_evenly(keyframes, 4), base_dir)]
    return llm.parse(system=CLASSIFY_SYSTEM, content=content, schema=Classification,
                     max_tokens=4000, effort=effort)


def summarize(
    llm: LLMClient,
    kind: str,
    segments: list[ScriptSegment],
    keyframes: list[Keyframe],
    base_dir: Path,
    duration: float,
    effort: str = "medium",
) -> VlogSummary | MeetingSummary:
    schema = MeetingSummary if kind == "meeting" else VlogSummary
    system = MEETING_SYSTEM if kind == "meeting" else VLOG_SYSTEM
    header = (
        f"Video length: {fmt_ts(duration)}\n\n"
        f"Transcript:\n{format_transcript(segments) or '(no speech)'}"
    )
    content = [{"type": "text", "text": header}, *_image_blocks(keyframes, base_dir)]
    return llm.parse(system=system, content=content, schema=schema, max_tokens=16000, effort=effort)


# ---------- quality checks (used by tests and eval) ----------
def meeting_problems(summary: MeetingSummary, duration: float, min_points_per_10min: float = 3.0) -> list[str]:
    """Checks the 'detailed meeting summary' requirement. Empty list = passes."""
    problems = []
    points = [p for t in summary.topics for p in t.points]
    if not summary.topics:
        problems.append("no topics")
    for p in points:
        if not p.speaker.strip():
            problems.append(f"point without speaker: {p.content[:40]!r}")
        t = parse_ts(p.timestamp)
        if t is None or t < 0 or t > duration + 5:
            problems.append(f"bad timestamp {p.timestamp!r}")
    for a in summary.action_items:
        if parse_ts(a.timestamp) is None:
            problems.append(f"action item with bad timestamp {a.timestamp!r}")
    needed = min_points_per_10min * duration / 600
    if len(points) < needed:
        problems.append(f"only {len(points)} points; need >= {needed:.1f} for {fmt_ts(duration)}")
    return problems


# ---------- Markdown ----------
def render_script(segments: list[ScriptSegment]) -> str:
    return _env.get_template("script.md.j2").render(segments=segments)


def render_summary(video: VideoSummary) -> str:
    if video.meeting is not None:
        return _env.get_template("meeting.md.j2").render(v=video, s=video.meeting)
    if video.vlog is not None:
        return _env.get_template("vlog.md.j2").render(v=video, s=video.vlog)
    raise ValueError(f"{video.video_id} has no summary")


def _demote_headings(md: str) -> str:
    return re.sub(r"^(#+) ", lambda m: "#" + m.group(1) + " ", md, flags=re.MULTILINE)


def render_diary(date: str, videos: list[VideoSummary]) -> str:
    entries = [_demote_headings(render_summary(v)) for v in videos]
    action_items = []
    for v in videos:
        items = v.meeting.action_items if v.meeting else (v.vlog.action_items if v.vlog else [])
        for it in items:
            action_items.append({"video": v.vlog.title if v.vlog else v.meeting.title, "item": it})
    return _env.get_template("diary.md.j2").render(date=date, videos=videos, entries=entries,
                                                   action_items=action_items)


def load_summaries(out_root: Path, date: str) -> list[VideoSummary]:
    """Summaries for one date. Unreadable or outdated summary files are skipped,
    so one bad file can't break every daily diary."""
    found = []
    for path in sorted(out_root.glob("*/summary.json")):
        try:
            raw = json.loads(path.read_text())
            if raw.get("date") != date:
                continue
            found.append(VideoSummary.model_validate(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"Skipping {path}: {e.__class__.__name__}")
    return found
