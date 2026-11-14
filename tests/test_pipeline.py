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


def test_diarization_runs_alongside_transcription(format_samples, tmp_path):
    import threading

    started, threads, steps = threading.Event(), {}, []

    def slow_transcribe(wav, settings, **kw):
        threads["asr"] = threading.current_thread().name
        assert started.wait(5), "diarization should start before transcription finishes"
        return fake_transcribe(wav, settings)

    def diarize(wav, settings, **kw):
        threads["diar"] = threading.current_thread().name
        started.set()
        return fake_diarize(wav, settings)

    components = Components(transcribe=slow_transcribe, diarize=diarize, visual=fake_visual, index=fake_index)
    folder = process_video(format_samples["mp4"], "2026-11-03", tmp_path, Settings(parallel_audio_mode="true"),
                           llm=FakeLLM(), components=components, parallel=True, log=lambda m: None,
                           on_step=steps.append)
    assert threads["asr"] != threads["diar"]
    script = json.loads((folder / "script.json").read_text())
    assert [s["speaker"] for s in script] == ["SPEAKER_00", "SPEAKER_01"]
    assert (folder / "diarization.json").exists()
    tl = json.loads((folder / "timeline.json").read_text())
    diar = next(s for s in tl["spans"] if s["name"] == "diarize")
    assert diar["meta"] == {"speakers": 2, "parallel": True}
    assert tl["config"]["parallel_audio"] is True
    assert steps[:3] == ["validating", "extracting_audio", "transcribing"]


def test_parallel_audio_setting():
    assert Settings(device="cpu", diarization_device="cpu").parallel_audio is False
    assert Settings(device="cuda", diarization_device="cuda").parallel_audio is True
    assert Settings(device="cuda", diarization_device="cuda", parallel_audio_mode="false").parallel_audio is False
    assert Settings(device="cpu", diarization_device="cpu", parallel_audio_mode="true").parallel_audio is True


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


def test_timeline_has_every_step_with_llm_details(format_samples, tmp_path):
    llm = FakeLLM("meeting")
    llm.last_usage = {"model": "claude-test", "input_tokens": 1200, "output_tokens": 300}
    folder = run(format_samples["mp4"], tmp_path, llm)
    tl = json.loads((folder / "timeline.json").read_text())
    assert tl["status"] == "done" and tl["video_duration"] > 0 and tl["source"] == "cli"
    names = [s["name"] for s in tl["spans"]]
    for name in ["probe", "extract_audio", "transcribe", "diarize", "visual", "merge_and_sync",
                 "classify", "summarize", "render", "index"]:
        assert name in names, name
    classify = next(s for s in tl["spans"] if s["name"] == "classify")
    assert classify["kind"] == "llm" and classify["meta"]["input_tokens"] == 1200
    assert classify["label"] == "Classify meeting / vlog (LLM)"
    assert all(s["end"] >= s["start"] for s in tl["spans"])
    runs = (tmp_path / "_logs" / "runs.jsonl").read_text().splitlines()
    assert len(runs) == 1 and json.loads(runs[0])["video_id"] == folder.name


def test_rerun_marks_reused_steps(format_samples, tmp_path):
    run(format_samples["mp4"], tmp_path, FakeLLM())
    folder = run(format_samples["mp4"], tmp_path, FakeLLM())
    spans = {s["name"]: s for s in json.loads((folder / "timeline.json").read_text())["spans"]}
    for name in ["extract_audio", "transcribe", "diarize", "visual"]:
        assert spans[name]["kind"] == "cached" and spans[name]["duration"] == 0
    assert len((tmp_path / "_logs" / "runs.jsonl").read_text().splitlines()) == 2  # history kept


def test_failed_run_is_logged(format_samples, tmp_path):
    def broken(wav, settings, **kw):
        raise RuntimeError("model crashed")
    import pytest
    with pytest.raises(RuntimeError):
        process_video(format_samples["mp4"], "2026-11-03", tmp_path, Settings(), llm=FakeLLM(),
                      components=Components(transcribe=broken, diarize=fake_diarize, visual=fake_visual,
                                            index=fake_index), parallel=False, log=lambda m: None)
    record = json.loads((tmp_path / "_logs" / "runs.jsonl").read_text())
    assert (record["status"], record["error"]) == ("failed", "model crashed")
    assert [s["name"] for s in record["spans"]][:2] == ["probe", "extract_audio"]
