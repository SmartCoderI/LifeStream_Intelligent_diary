"""F3: merging ASR words with diarization segments into speaker turns."""

from lifestream.processing.asr import words_from_aligned
from lifestream.processing.merge import assign_speakers, build_turns, group_turns
from lifestream.schemas import UNKNOWN_SPEAKER, SpeakerSegment, Word


def W(start, end, text="w", speaker=None):
    return Word(start=start, end=end, text=text, speaker=speaker)


def S(start, end, speaker):
    return SpeakerSegment(start=start, end=end, speaker=speaker)


def speakers(words):
    return [w.speaker for w in words]


def test_back_and_forth_turns():
    words = [W(0.0, 0.4, "hi"), W(0.5, 0.9, "there"), W(1.2, 1.6, "hello"), W(1.7, 2.0, "back")]
    segs = [S(0.0, 1.0, "A"), S(1.1, 2.1, "B")]
    turns = build_turns(words, segs)
    assert [(t.speaker, t.text) for t in turns] == [("A", "hi there"), ("B", "hello back")]
    assert turns[0].start == 0.0 and turns[1].end == 2.0


def test_word_on_boundary_goes_to_larger_overlap():
    # word 0.8-1.4 overlaps A by 0.2 s and B by 0.4 s
    out = assign_speakers([W(0.8, 1.4)], [S(0.0, 1.0, "A"), S(1.0, 2.0, "B")])
    assert speakers(out) == ["B"]


def test_overlapping_speech_picks_dominant_speaker():
    # A talks 0-3, B interrupts 2-4. Before the overlap -> A; inside the
    # overlap, the word overlaps both and goes to the one it overlaps most;
    # after A stops -> B.
    out = assign_speakers(
        [W(1.0, 1.5), W(2.6, 3.2), W(3.1, 3.5)],
        [S(0.0, 3.0, "A"), S(2.0, 4.0, "B")],
    )
    assert speakers(out) == ["A", "B", "B"]


def test_gap_uses_nearest_within_tolerance_else_unknown():
    segs = [S(0.0, 1.0, "A"), S(5.0, 6.0, "B")]
    out = assign_speakers([W(1.2, 1.4), W(4.7, 4.9), W(2.5, 3.0)], segs, nearest_tolerance=0.5)
    assert speakers(out) == ["A", "B", UNKNOWN_SPEAKER]


def test_no_segments_all_unknown():
    assert speakers(assign_speakers([W(0, 1), W(1, 2)], [])) == [UNKNOWN_SPEAKER] * 2


def test_long_pause_splits_same_speaker_turn():
    words = [W(0.0, 0.5, "a", "A"), W(0.6, 1.0, "b", "A"), W(3.0, 3.5, "c", "A")]
    assert [t.text for t in group_turns(words, max_gap=1.0)] == ["a b", "c"]


def test_single_speaker_and_empty_input():
    assert len(build_turns([W(0, 1), W(1.1, 2)], [S(0, 2, "A")])) == 1
    assert build_turns([], [S(0, 2, "A")]) == []


def test_many_words_fast():
    words = [W(i * 0.3, i * 0.3 + 0.25) for i in range(20_000)]
    segs = [S(i * 3.0, i * 3.0 + 3.0, f"S{i % 4}") for i in range(2_000)]
    out = assign_speakers(words, segs)
    assert all(w.speaker != UNKNOWN_SPEAKER for w in out)


def test_aligned_words_missing_timestamps_are_filled():
    aligned = {"segments": [{"start": 0.0, "words": [
        {"word": "I", "start": 0.0, "end": 0.2},
        {"word": "paid", "start": 0.3, "end": 0.6},
        {"word": "$20", "start": None, "end": None},
        {"word": "today", "start": 1.2, "end": 1.6},
    ]}]}
    words = words_from_aligned(aligned)
    assert [w.text for w in words] == ["I", "paid", "$20", "today"]
    assert words[2].start == 0.6 and words[2].end == 1.2
    assert all(w.end >= w.start for w in words)
