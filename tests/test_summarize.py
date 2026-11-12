"""F6: classification + summary JSON validation and Markdown format."""

import pytest
from pydantic import ValidationError

from lifestream import summarize
from lifestream.schemas import (
    Classification, Keyframe, Location, MeetingPoint, MeetingSummary, MeetingTopic,
    ScriptSegment, VideoSummary, VlogSummary,
)

from fakes import FakeLLM


def seg(start, end, speaker, text="words"):
    return ScriptSegment(start=start, end=end, speaker=speaker, text=text)


SEGMENTS = [seg(0, 30, "SPEAKER_00"), seg(31, 60, "SPEAKER_01"), seg(400, 420, "SPEAKER_00")]


def video(kind, llm=None, duration=60.0, date="2026-11-03", vid="v1"):
    llm = llm or FakeLLM(kind)
    body = summarize.summarize(llm, kind, SEGMENTS, [], None, duration)
    return VideoSummary(
        video_id=vid, date=date, duration=duration,
        classification=Classification(type=kind, confidence="high", reason="r"),
        vlog=body if kind == "vlog" else None, meeting=body if kind == "meeting" else None,
    )


def test_classify_sends_only_opening_window_and_speaker_count():
    llm = FakeLLM("meeting")
    result = summarize.classify(llm, SEGMENTS, [], None, duration=420, window_s=300)
    assert result.type == "meeting"
    text = llm.calls[0]["content"][0]["text"]
    assert "Distinct speakers (acoustic diarization): 2" in text
    assert "[00:06:40]" not in text  # segment after the 5-minute window is excluded
    assert llm.calls[0]["effort"] == "low"


def test_summary_uses_schema_for_type():
    llm = FakeLLM("meeting")
    assert isinstance(summarize.summarize(llm, "meeting", SEGMENTS, [], None, 60), MeetingSummary)
    assert isinstance(summarize.summarize(llm, "vlog", SEGMENTS, [], None, 60), VlogSummary)


def test_meeting_point_without_speaker_or_timestamp_fails_schema():
    with pytest.raises(ValidationError):
        MeetingPoint(content="no speaker or time")


def test_meeting_markdown_has_required_sections():
    md = summarize.render_summary(video("meeting"))
    for header in ("## Location", "## Activity", "## Action Items"):
        assert header in md
    assert "**[00:00:01] SPEAKER_00:** point 1" in md
    assert "owner: SPEAKER_01, agreed at 00:00:05, due Thursday" in md
    assert "The team reviewed the redesign." in md
    assert "## Key Decisions\n\n- Ship the navigation bar on Friday" in md


def test_vlog_markdown_has_required_sections_and_none_for_empty_items():
    md = summarize.render_summary(video("vlog"))
    for header in ("## Location", "## Activity", "## Action Items"):
        assert header in md
    assert "City park *(inferred)*" in md
    assert md.split("## Action Items")[1].strip() == "- None"


def test_two_videos_same_date_combine_into_one_diary():
    md = summarize.render_diary("2026-11-03", [video("meeting", vid="a"), video("vlog", vid="b")])
    assert md.startswith("# Daily Summary: 2026-11-03")
    assert "2 videos recorded" in md
    assert "## Frontend sync" in md and "## Morning walk" in md  # headings demoted one level
    assert "### Action Items" in md
    assert "- [ ] Fix the logo width *(Frontend sync)*" in md.split("## All Action Items")[1]


def test_detail_check_flags_thin_meeting_summary():
    thin = video("meeting", llm=FakeLLM("meeting", points=2), duration=1200)
    problems = summarize.meeting_problems(thin.meeting, thin.duration, min_points_per_10min=3)
    assert any("only 2 points" in p for p in problems)
    rich = video("meeting", llm=FakeLLM("meeting", points=6), duration=1200)
    assert summarize.meeting_problems(rich.meeting, rich.duration) == []


def test_detail_check_flags_bad_timestamps():
    m = MeetingSummary(title="t", overview="o", decisions=[], location=Location(value="Unknown", source="unknown"),
                       attendees=["A"], action_items=[],
                       topics=[MeetingTopic(title="x", points=[
                           MeetingPoint(speaker="A", timestamp="soon", content="c")])])
    assert any("bad timestamp" in p for p in summarize.meeting_problems(m, 60, 0))


def test_script_markdown_table_escapes_pipes():
    md = summarize.render_script([seg(65, 70, "SPEAKER_00", "a | b")])
    assert "| 00:01:05 | SPEAKER_00 | a \\| b |" in md


def test_keyframes_sent_as_images(tmp_path):
    (tmp_path / "keyframes").mkdir()
    (tmp_path / "keyframes" / "f.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    llm = FakeLLM("vlog")
    summarize.summarize(llm, "vlog", SEGMENTS, [Keyframe(time=5, path="keyframes/f.jpg")], tmp_path, 60)
    blocks = llm.calls[0]["content"]
    assert [b["type"] for b in blocks] == ["text", "text", "image"]
    assert blocks[1]["text"] == "Keyframe at 00:00:05:"


def test_load_summaries_skips_other_dates_and_bad_files(tmp_path):
    good = video("meeting", vid="good")
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "summary.json").write_text(good.model_dump_json())
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "summary.json").write_text('{"date": "2026-11-03", "video_id": "old"}')
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "summary.json").write_text("{not json")
    assert [v.video_id for v in summarize.load_summaries(tmp_path, "2026-11-03")] == ["good"]
