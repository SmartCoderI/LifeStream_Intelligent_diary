"""Model comparison on saved outputs, with the fake LLM (no API calls)."""

import json

from fakes import FakeLLM

from lifestream.config import Settings
from lifestream.evaluation.models import compare_models, labelled_videos, parse_spec
from lifestream.intelligence.llm import SERVER_FALLBACK_MODELS, AnthropicLLM, cost_usd
from lifestream.schemas import ScriptSegment


def _processed(out_root, vid="team-sync"):
    folder = out_root / vid
    folder.mkdir(parents=True)
    segments = [ScriptSegment(start=i * 20.0, end=i * 20.0 + 15, speaker=f"SPEAKER_0{i % 2}",
                              text=f"point number {i}") for i in range(6)]
    (folder / "script.json").write_text(json.dumps([s.model_dump() for s in segments]))
    (folder / "keyframes.json").write_text("[]")
    (folder / "metadata.json").write_text(json.dumps({"video_id": vid, "date": "2026-11-12", "duration": 120.0}))
    return folder


def test_cost():
    assert cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == 24.0
    assert round(cost_usd("claude-haiku-5-5", 4819, 3619), 5) == 0.00229
    assert cost_usd("some-other-model", 10, 10) is None


def test_labelled_videos_only_includes_processed(tmp_path):
    labels, out = tmp_path / "labels", tmp_path / "out"
    labels.mkdir()
    (labels / "a.yaml").write_text("video_id: team-sync\ntype: meeting\n")
    (labels / "b.yaml").write_text("video_id: not-processed\ntype: vlog\n")
    _processed(out)
    assert labelled_videos(labels, out) == {"team-sync": "meeting"}


def test_compare_models_saves_each_summary_and_records_usage(tmp_path):
    out, save = tmp_path / "out", tmp_path / "save"
    _processed(out)

    def make_llm(model):
        llm = FakeLLM(kind="meeting")
        original = llm.parse

        def parse(**kw):
            llm.last_usage = {"model": model, "input_tokens": 1000, "output_tokens": 500}
            return original(**kw)
        llm.parse = parse
        return llm

    runs = compare_models(["claude-sonnet-5-5", "claude-haiku-5-5"], {"team-sync": "meeting"}, out, save,
                          Settings(), make_llm, log=lambda *_: None)
    assert [r.model for r in runs] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    sonnet = runs[0]
    assert sonnet.actual == "meeting" and sonnet.input_tokens == 2000 and sonnet.output_tokens == 1000
    assert sonnet.cost_usd == cost_usd("claude-sonnet-5-5", 2000, 1000)
    assert [c["step"] for c in sonnet.calls] == ["classify", "summarize"]
    for model in ("claude-sonnet-5-5", "claude-haiku-5-5"):
        assert (save / model / "team-sync" / "summary.md").exists()
    saved = json.loads((save / "results.json").read_text())
    assert len(saved) == 2 and "passed" in saved[0]
    assert not (out / "team-sync" / "summary.json").exists()   # data/out untouched


def test_fallback_only_sent_to_models_that_support_it():
    class Client:
        def __init__(self):
            self.kwargs = None
            self.beta = self
            self.messages = self

        def parse(self, **kwargs):
            self.kwargs = kwargs
            raise RuntimeError("stop here")

    for model, expect in [("claude-haiku-5-5", False), ("claude-sonnet-5-5", True), ("claude-opus-5-5", True)]:
        client = Client()
        try:
            AnthropicLLM(model, client=client).parse(system="s", content=[], schema=dict, max_tokens=10, effort="low")
        except RuntimeError:
            pass
        assert ("fallbacks" in client.kwargs) is expect, model
        assert (model in SERVER_FALLBACK_MODELS) is expect


def test_model_at_effort(tmp_path):
    assert parse_spec("claude-opus-5-5@low") == ("claude-opus-5-5", "low")
    assert parse_spec("claude-haiku-5-5") == ("claude-haiku-5-5", None)
    out, save = tmp_path / "out", tmp_path / "save"
    _processed(out)
    seen = []

    def make_llm(model):
        llm = FakeLLM(kind="meeting")
        original = llm.parse

        def parse(**kw):
            seen.append((model, kw["schema"].__name__, kw["effort"]))
            return original(**kw)
        llm.parse = parse
        return llm

    (run,) = compare_models(["claude-opus-5-5@low"], {"team-sync": "meeting"}, out, save, Settings(),
                            make_llm, log=lambda *_: None)
    assert run.model == "claude-opus-5-5@low"
    assert seen == [("claude-opus-5-5", "Classification", "low"), ("claude-opus-5-5", "MeetingSummary", "low")]
    assert (save / "claude-opus-5-5@low" / "team-sync" / "summary.md").exists()
