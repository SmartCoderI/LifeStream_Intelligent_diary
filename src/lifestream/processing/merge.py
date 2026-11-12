"""F3: merge ASR words with diarization segments into speaker turns.

Rules:
1. A word goes to the speaker segment it overlaps most.
2. A word that overlaps no segment goes to the nearest segment if the gap is
   at most `nearest_tolerance` seconds, otherwise to UNKNOWN.
3. Consecutive words from the same speaker form one turn, unless the silence
   between them is `max_gap` seconds or longer.
"""

from __future__ import annotations

from bisect import bisect_right

from ..schemas import UNKNOWN_SPEAKER, SpeakerSegment, Turn, Word


def assign_speakers(
    words: list[Word],
    segments: list[SpeakerSegment],
    nearest_tolerance: float = 0.5,
) -> list[Word]:
    """Return copies of `words` with `.speaker` set."""
    if not segments:
        return [w.model_copy(update={"speaker": UNKNOWN_SPEAKER}) for w in words]

    segs = sorted(segments, key=lambda s: (s.start, s.end))
    starts = [s.start for s in segs]
    # running max of segment ends, so we know when to stop scanning backwards
    max_end: list[float] = []
    running = float("-inf")
    for s in segs:
        running = max(running, s.end)
        max_end.append(running)

    out: list[Word] = []
    for w in words:
        best_speaker = UNKNOWN_SPEAKER
        best_overlap = 0.0
        best_gap = float("inf")
        nearest_speaker = None

        # candidates: segments starting before w.end + tolerance and not ending
        # before w.start - tolerance
        j = bisect_right(starts, w.end + nearest_tolerance) - 1
        while j >= 0 and max_end[j] >= w.start - nearest_tolerance:
            s = segs[j]
            overlap = min(w.end, s.end) - max(w.start, s.start)
            # ties keep the first segment found (the later-starting one)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = s.speaker
            if overlap <= 0:
                gap = max(s.start - w.end, w.start - s.end, 0.0)
                if gap < best_gap:
                    best_gap = gap
                    nearest_speaker = s.speaker
            j -= 1

        if best_overlap <= 0:
            if nearest_speaker is not None and best_gap <= nearest_tolerance:
                best_speaker = nearest_speaker
            else:
                best_speaker = UNKNOWN_SPEAKER
        out.append(w.model_copy(update={"speaker": best_speaker}))
    return out


def group_turns(words: list[Word], max_gap: float = 1.0) -> list[Turn]:
    """Merge consecutive same-speaker words into turns."""
    turns: list[Turn] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            turns.append(Turn(
                start=current[0].start,
                end=max(w.end for w in current),
                speaker=current[0].speaker or UNKNOWN_SPEAKER,
                text=" ".join(w.text for w in current),
            ))

    for w in words:
        if current and (w.speaker != current[-1].speaker or w.start - current[-1].end >= max_gap):
            flush()
            current = []
        current.append(w)
    flush()
    return turns


def build_turns(
    words: list[Word],
    segments: list[SpeakerSegment],
    nearest_tolerance: float = 0.5,
    max_gap: float = 1.0,
) -> list[Turn]:
    return group_turns(assign_speakers(words, segments, nearest_tolerance), max_gap)
