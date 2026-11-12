"""F8: chunk boundaries and summary paragraphs."""

from lifestream.evaluation.retrieval import scenario
from lifestream.memory.chunking import summary_chunks, transcript_chunks
from lifestream.schemas import ScriptSegment


def turns(n, length=20.0, gap=1.0):
    return [ScriptSegment(start=i * (length + gap), end=i * (length + gap) + length,
                          speaker=f"S{i % 2}", text=f"turn {i}.") for i in range(n)]


def test_short_video_is_one_chunk():
    chunks = transcript_chunks("v", "2026-11-04", "Title", "meeting", turns(4))
    assert len(chunks) == 1
    assert chunks[0].start == 0 and chunks[0].end == 3 * 21 + 20


def test_chunks_cut_at_turn_boundaries_with_overlap():
    script = turns(30)  # ~10 minutes
    chunks = transcript_chunks("v", "2026-11-04", "Title", "meeting", script, target_s=120, overlap_s=30)
    starts = {t.start for t in script}
    ends = {t.end for t in script}
    for c in chunks:
        assert c.start in starts and c.end in ends  # never mid-turn
        assert c.end - c.start <= 120 + 21
    for a, b in zip(chunks, chunks[1:]):
        assert b.start < a.end  # overlap
        assert b.start > a.start  # always moves forward
    assert chunks[-1].end == script[-1].end  # nothing dropped


def test_long_monologue_turn_is_split():
    long = [ScriptSegment(start=0, end=600, speaker="S0", text="Sentence one. " * 200)]
    chunks = transcript_chunks("v", "2026-11-04", "T", "vlog", long, target_s=120)
    assert len(chunks) >= 4


def test_chunk_text_has_header_but_specific_parts_embed_content_only():
    c = transcript_chunks("v", "2026-11-04", "Team sync", "meeting", turns(2))[0]
    assert c.text.startswith("Team sync | 2026-11-04 | meeting | Speakers: S0, S1")
    assert c.to_embed().startswith("[00:00:00] S0: turn 0.")


def test_summary_paragraphs_meeting():
    summary, _ = scenario()[0]
    chunks = summary_chunks(summary)
    assert [c.section for c in chunks] == [
        "Summary", "Topic: Frontend: navigation bar and mobile layout", "Topic: Dashboard chart loading",
        "Key Decisions", "Action Items"]
    overview, topic = chunks[0], chunks[1]
    assert overview.to_embed() == overview.text  # overview keeps the video-level header
    assert topic.to_embed().startswith("Topic: Frontend")
    assert (topic.start, topic.end) == (70.0, 420.0)  # first point to the next topic
    assert "Location: Office" in overview.text


def test_summary_paragraphs_vlog():
    summary, _ = scenario()[-1]
    sections = [c.section for c in summary_chunks(summary)]
    assert sections == ["Summary", "Activity 00:00:30"]
