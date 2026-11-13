"""QwenLLM against a fake OpenAI-compatible client (no network, no key)."""

from types import SimpleNamespace

import pytest

from lifestream.config import Settings
from lifestream.intelligence.llm import AnthropicLLM, LLMError, QwenLLM, make_llm
from lifestream.schemas import Classification


def _response(text, finish="stop", prompt=100, completion=20):
    return SimpleNamespace(
        model="qwen-test",
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))],
    )


class FakeOpenAI:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def _qwen(fake, **kw):
    return QwenLLM("qwen-test", "http://fake/v1", "key", client=fake, **kw)


CONTENT = [
    {"type": "text", "text": "Transcript..."},
    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
]


def test_converts_images_and_puts_schema_in_system_prompt():
    fake = FakeOpenAI(_response('{"type": "meeting", "confidence": "high", "reason": "agenda"}'))
    result = _qwen(fake).parse(system="Classify.", content=CONTENT, schema=Classification,
                               max_tokens=16000, effort="low")

    assert result.type == "meeting"
    call = fake.calls[0]
    system, user = call["messages"]
    assert system["role"] == "system" and '"confidence"' in system["content"]
    assert user["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}
    assert call["max_tokens"] == 8192          # capped
    assert call["response_format"] == {"type": "json_object"}


def test_tolerates_fences_and_thinking():
    text = '<think>hmm</think>\n```json\n{"type": "vlog", "confidence": "low", "reason": "outdoors"}\n```'
    llm = _qwen(FakeOpenAI(_response(text)))
    assert llm.parse(system="s", content=CONTENT, schema=Classification, max_tokens=100, effort="low").type == "vlog"


def test_retries_once_on_invalid_json_and_sums_usage():
    fake = FakeOpenAI(_response('{"type": "podcast"}'),
                      _response('{"type": "meeting", "confidence": "medium", "reason": "r"}'))
    llm = _qwen(fake)
    assert llm.parse(system="s", content=CONTENT, schema=Classification, max_tokens=100, effort="low").type == "meeting"
    assert len(fake.calls) == 2
    assert fake.calls[1]["messages"][-1]["content"].startswith("That reply was not valid")
    assert llm.last_usage == {"model": "qwen-test", "input_tokens": 200, "output_tokens": 40, "cache_read_tokens": 0}


def test_gives_up_after_second_invalid_reply():
    llm = _qwen(FakeOpenAI(_response("no json here"), _response("still none")))
    with pytest.raises(LLMError, match="expected format"):
        llm.parse(system="s", content=CONTENT, schema=Classification, max_tokens=100, effort="low")


def test_cut_off_reply_is_an_error():
    llm = _qwen(FakeOpenAI(_response('{"type": "mee', finish="length")))
    with pytest.raises(LLMError, match="QWEN_MAX_TOKENS"):
        llm.parse(system="s", content=CONTENT, schema=Classification, max_tokens=100, effort="low")


def test_provider_switch(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert Settings().llm_provider == "claude"            # Claude is the default
    assert isinstance(make_llm(Settings()), AnthropicLLM)
    assert Settings().active_llm_model == Settings().llm_model
    for provider in ("qwen", "gpt"):                      # Qwen is commented out in make_llm
        with pytest.raises(LLMError, match="LLM_PROVIDER"):
            make_llm(Settings(llm_provider=provider))
