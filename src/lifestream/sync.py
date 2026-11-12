"""F5: attach keyframes to the speaker turn they belong to, by timestamp."""

from __future__ import annotations

from bisect import bisect_right

from .schemas import Keyframe, ScriptSegment, Turn


def attach_frames(turns: list[Turn], keyframes: list[Keyframe]) -> list[ScriptSegment]:
    """Each frame goes to the turn whose time range contains it; otherwise to the
    nearest turn (frames before the first turn go to the first, after the last
    go to the last)."""
    segments = [ScriptSegment(start=t.start, end=t.end, speaker=t.speaker, text=t.text) for t in turns]
    if not segments:
        return segments

    starts = [s.start for s in segments]
    for kf in sorted(keyframes, key=lambda k: k.time):
        i = bisect_right(starts, kf.time) - 1  # last turn starting at or before the frame
        if i >= 0 and kf.time <= segments[i].end:
            target = i
        else:
            before = i if i >= 0 else None
            after = i + 1 if i + 1 < len(segments) else None
            if before is None:
                target = after
            elif after is None:
                target = before
            else:
                gap_before = kf.time - segments[before].end
                gap_after = segments[after].start - kf.time
                target = before if gap_before <= gap_after else after
        segments[target].frames.append(kf.path)
    return segments
