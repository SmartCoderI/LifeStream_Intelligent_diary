"""F5: mapping keyframes to speaker turns by timestamp."""

from lifestream.schemas import Keyframe, Turn
from lifestream.processing.sync import attach_frames


def T(start, end, speaker="A"):
    return Turn(start=start, end=end, speaker=speaker, text="...")


def K(t):
    return Keyframe(time=t, path=f"keyframes/{t}.jpg")


def frames(segments):
    return [s.frames for s in segments]


def test_frame_inside_turn():
    assert frames(attach_frames([T(0, 5), T(6, 10)], [K(2), K(8)])) == [["keyframes/2.jpg"], ["keyframes/8.jpg"]]


def test_frame_between_turns_goes_to_nearest():
    out = attach_frames([T(0, 5), T(10, 15)], [K(6), K(9)])
    assert frames(out) == [["keyframes/6.jpg"], ["keyframes/9.jpg"]]


def test_frames_before_first_and_after_last_turn():
    out = attach_frames([T(5, 10), T(12, 15)], [K(1), K(20)])
    assert frames(out) == [["keyframes/1.jpg"], ["keyframes/20.jpg"]]


def test_several_frames_in_one_turn_keep_time_order():
    out = attach_frames([T(0, 30)], [K(20), K(5), K(12)])
    assert frames(out) == [["keyframes/5.jpg", "keyframes/12.jpg", "keyframes/20.jpg"]]


def test_no_turns():
    assert attach_frames([], [K(1)]) == []
