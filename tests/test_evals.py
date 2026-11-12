"""M1 metric code: label mapping, segment accuracy, DER, RTTM round trip."""

import pytest

from lifestream.evals import best_mapping, der, load_rttm, segment_accuracy, write_rttm
from lifestream.schemas import SpeakerSegment


def S(start, end, speaker):
    return SpeakerSegment(start=start, end=end, speaker=speaker)


REF = [S(0, 10, "alice"), S(10, 20, "bob"), S(18, 25, "carol"), S(25, 30, "alice")]


def test_perfect_hypothesis_with_different_labels():
    hyp = [S(0, 10, "SPEAKER_02"), S(10, 20, "SPEAKER_00"), S(18, 25, "SPEAKER_01"), S(25, 30, "SPEAKER_02")]
    assert best_mapping(REF, hyp) == {"SPEAKER_02": "alice", "SPEAKER_00": "bob", "SPEAKER_01": "carol"}
    result = segment_accuracy(REF, hyp)
    assert result.accuracy == 1.0
    assert der(REF, hyp) == pytest.approx(0.0, abs=1e-6)


def test_one_wrong_segment():
    hyp = [S(0, 10, "X"), S(10, 20, "Y"), S(18, 25, "Y"), S(25, 30, "X")]  # carol missed
    result = segment_accuracy(REF, hyp)
    assert (result.correct, result.segments) == (3, 4)
    assert result.wrong[0]["speaker"] == "carol"


def test_segment_needs_majority_coverage():
    # X mostly overlaps bob (10 s) -> X=bob, so Y=alice. Alice's segment is
    # only 40% labeled Y -> wrong; bob's is fully X -> right.
    hyp = [S(0, 4, "Y"), S(4, 10, "X"), S(10, 20, "X")]
    result = segment_accuracy([S(0, 10, "alice"), S(10, 20, "bob")], hyp)
    assert result.mapping == {"X": "bob", "Y": "alice"}
    assert (result.correct, result.segments) == (1, 2)


def test_rttm_round_trip(tmp_path):
    path = tmp_path / "x.rttm"
    write_rttm(path, REF)
    loaded = load_rttm(path)
    assert [(round(s.start, 3), round(s.end, 3), s.speaker) for s in loaded] == \
           [(s.start, s.end, s.speaker) for s in REF]
