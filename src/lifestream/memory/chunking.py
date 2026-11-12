"""F8: split a processed video into searchable chunks.

Two kinds:
- transcript: ~2 minutes of speech, cut at speaker-turn boundaries, with ~30 s
  overlap so an exchange that crosses a cut is still found as a whole.
- summary: one chunk per paragraph of the video's summary (overview, each
  topic, decisions, action items...), so a question can return the specific
  paragraph that answers it.

Each chunk's display text starts with a short header (title, date, type,
speakers). Only the overview chunk embeds that header, as the video-level
description; every other chunk embeds just its own section, so specific
paragraphs of one video don't all look alike to the search.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import ScriptSegment, VideoSummary
from ..timing import fmt_ts, parse_ts

ChunkKind = Literal["transcript", "summary"]


class Chunk(BaseModel):
    video_id: str
    date: str
    kind: ChunkKind
    section: str
    start: float
    end: float
    speakers: list[str] = Field(default_factory=list)
    text: str  # shown to people and the LLM (header + section + content)
    embed_text: str = ""  # what gets embedded; defaults to text

    def to_embed(self) -> str:
        return self.embed_text or self.text


def _header(title: str, date: str, kind: str, speakers: list[str]) -> str:
    who = f" | Speakers: {', '.join(speakers)}" if speakers else ""
    return f"{title} | {date} | {kind}{who}"


def _split_long_turn(seg: ScriptSegment, max_s: float) -> list[ScriptSegment]:
    """Split one very long turn (e.g. a monologue) into sentence groups, with times
    estimated by text position."""
    length = seg.end - seg.start
    if length <= max_s:
        return [seg]
    sentences = re.split(r"(?<=[.!?])\s+", seg.text.strip()) or [seg.text]
    parts = max(2, int(length // max_s) + 1)
    per = max(1, len(sentences) // parts)
    groups = [" ".join(sentences[i:i + per]) for i in range(0, len(sentences), per)]
    total_chars = sum(len(g) for g in groups) or 1
    out, t = [], seg.start
    for g in groups:
        dur = length * len(g) / total_chars
        out.append(ScriptSegment(start=t, end=t + dur, speaker=seg.speaker, text=g))
        t += dur
    return out


def transcript_chunks(
    video_id: str,
    date: str,
    title: str,
    kind_label: str,
    script: list[ScriptSegment],
    target_s: float = 120.0,
    overlap_s: float = 30.0,
) -> list[Chunk]:
    turns = [part for seg in script for part in _split_long_turn(seg, target_s)]
    chunks: list[Chunk] = []
    i = 0
    while i < len(turns):
        start = turns[i].start
        j = i
        while j < len(turns) and (turns[j].start - start < target_s or j == i):
            j += 1
        group = turns[i:j]
        speakers = list(dict.fromkeys(t.speaker for t in group))
        lines = "\n".join(f"[{fmt_ts(t.start)}] {t.speaker}: {t.text}" for t in group)
        chunks.append(Chunk(
            video_id=video_id, date=date, kind="transcript",
            section=f"Transcript {fmt_ts(group[0].start)}-{fmt_ts(group[-1].end)}",
            start=group[0].start, end=group[-1].end, speakers=speakers,
            text=f"{_header(title, date, kind_label, speakers)}\n{lines}", embed_text=lines,
        ))
        if j >= len(turns):
            break
        # next chunk starts at the first turn inside the overlap window (always moves forward)
        end = group[-1].end
        k = next((n for n in range(i + 1, j) if turns[n].start >= end - overlap_s), j)
        i = k
    return chunks


def _times(values: list[str]) -> list[float]:
    return [t for t in (parse_ts(v) for v in values) if t is not None]


def summary_chunks(video: VideoSummary) -> list[Chunk]:
    """One chunk per summary paragraph. `video` should already have speaker names applied."""
    m, v = video.meeting, video.vlog
    body = m or v
    if body is None:
        return []
    kind_label = video.classification.type
    speakers = m.attendees if m else []
    head = _header(body.title, video.date, kind_label, speakers)
    dur = video.duration

    def chunk(section: str, text: str, start: float, end: float, who: list[str]) -> Chunk:
        return Chunk(video_id=video.video_id, date=video.date, kind="summary", section=section,
                     start=start, end=max(start, end), speakers=who, text=f"{head}\n{section}\n{text}",
                     embed_text=f"{section}\n{text}")

    overview = body.overview
    if body.location.source != "unknown":
        overview += f"\nLocation: {body.location.value}"
    out = [chunk("Summary", overview, 0.0, dur, speakers)]
    out[0].embed_text = out[0].text  # the overview carries the video-level header

    if m:
        topic_times = []
        for t in m.topics:
            ts = _times([p.timestamp for p in t.points])
            topic_times.append(min(ts) if ts else None)
        for n, t in enumerate(m.topics):
            ts = _times([p.timestamp for p in t.points])
            start = min(ts) if ts else 0.0
            nxt = next((x for x in topic_times[n + 1:] if x is not None), dur)
            lines = "\n".join(f"[{p.timestamp}] {p.speaker}: {p.content}" for p in t.points)
            who = list(dict.fromkeys(p.speaker for p in t.points))
            out.append(chunk(f"Topic: {t.title}", lines, start, max(nxt, max(ts) if ts else start), who))
        if m.decisions:
            out.append(chunk("Key Decisions", "\n".join(f"- {d}" for d in m.decisions), 0.0, dur, speakers))
        if m.action_items:
            lines = "\n".join(
                f"- {a.task} (owner: {a.owner}{', due ' + a.due if a.due else ''}, at {a.timestamp})"
                for a in m.action_items)
            ts = _times([a.timestamp for a in m.action_items])
            out.append(chunk("Action Items", lines, min(ts) if ts else 0.0, max(ts) if ts else dur,
                             list(dict.fromkeys(a.owner for a in m.action_items))))
    else:
        acts = v.activities
        for n in range(0, len(acts), 5):  # groups of up to 5 activities
            group = acts[n:n + 5]
            ts = _times([a.time for a in group])
            following = _times([a.time for a in acts[n + 5:n + 6]])
            lines = "\n".join(f"[{a.time}] {a.description}" for a in group)
            out.append(chunk(f"Activity {group[0].time}", lines, min(ts) if ts else 0.0,
                             following[0] if following else dur, []))
        if v.action_items:
            lines = "\n".join(f"- {a.task}" + (f" ({a.owner})" if a.owner else "") for a in v.action_items)
            out.append(chunk("Action Items", lines, 0.0, dur, []))
    return out
