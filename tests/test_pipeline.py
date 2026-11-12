"""End-to-end pipeline wiring with fake models (real FFmpeg, no API keys)."""

import json

from lifestream.config import Settings
from lifestream.pipeline import Components, process_video, video_id_for

from fakes import FakeLLM, fake_diarize, fake_index, fake_transcribe, fake_visual

FAKES = Components(transcribe=fake_transcribe, diarize=fake_diarize, visual=fake_visual, index=fake_index)


def run(video, out, llm, date="2026-11-03", **kw):
    return process_video(video, date, out, Settings(), llm=llm, components=FAKES,
                         parallel=False, log=lambda m: None, **kw)


def test_writes_all_outputs(format_samples, tmp_path):
    folder = run(format_samples["mp4"], tmp_path, FakeLLM("meeting"))
    for name in ["metadata.json", "audio.wav", "words.json", "diarization.json", "scenes.json",
                 "keyframes.json", "script.json", "script.md", "summary.json", "summary.md", "timings.json"]:
        assert (folder / name).exists(), name
    script = json.loads((folder / "script.json").read_text())
    assert [s["speaker"] for s in script] == ["SPEAKER_00", "SPEAKER_01"]
    assert script[0]["frames"] == ["keyframes/none.jpg"]
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["classification"]["type"] == "meeting" and summary["meeting"] is not None
    timings = json.loads((folder / "timings.json").read_text())
    assert {"probe", "extract_audio", "transcribe", "diarize", "visual_branch", "classify", "summarize", "total"} <= set(timings)
    assert (tmp_path / "diaries" / "2026-11-03.md").exists()


def test_rerun_reuses_saved_steps(format_samples, tmp_path):
    run(format_samples["mp4"], tmp_path, FakeLLM())
    calls = []
    components = Components(transcribe=lambda *a: calls.append("asr") or [],
                            diarize=lambda *a, **k: calls.append("diar") or [],
                            visual=fake_visual, index=fake_index)
    process_video(format_samples["mp4"], "2026-11-03", tmp_path, Settings(), llm=FakeLLM(),
                  components=components, parallel=False, log=lambda m: None)
    assert calls == []


def test_parallel_visual_branch_in_separate_process(format_samples, tmp_path):
    folder = process_video(format_samples["mp4"], "2026-11-03", tmp_path, Settings(),
                           llm=FakeLLM(), components=FAKES, parallel=True, log=lambda m: None)
    assert json.loads((folder / "keyframes.json").read_text())[0]["path"] == "keyframes/none.jpg"


def test_daily_diary_combines_videos(format_samples, tmp_path):
    run(format_samples["mp4"], tmp_path, FakeLLM("meeting"))
    run(format_samples["mov"], tmp_path, FakeLLM("vlog"))
    run(format_samples["mkv"], tmp_path, FakeLLM("vlog"), date="2026-11-04")
    diary = (tmp_path / "diaries" / "2026-11-03.md").read_text()
    assert "2 videos recorded" in diary


def test_no_llm_stops_after_script(format_samples, tmp_path):
    folder = run(format_samples["mp4"], tmp_path, None, run_llm=False)
    assert (folder / "script.md").exists() and not (folder / "summary.json").exists()


def test_video_id():
    assert video_id_for("/x/Under the Hood: AI Economy.MP4") == "under-the-hood-ai-economy"
