"""Thin LLM wrapper so the pipeline can swap providers or use a fake in tests."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from ..config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    def parse(
        self,
        *,
        system: str,
        content: list[dict],
        schema: type[T],
        max_tokens: int,
        effort: str,
    ) -> T:
        """Send one user message (text + image blocks) and return a validated `schema` object."""
        ...


class AnthropicLLM:
    """Claude via the official SDK, with JSON output validated against a Pydantic model."""

    name = "Claude"

    def __init__(self, model: str, client=None):
        import anthropic

        self.model = model
        self.client = client or anthropic.Anthropic()
        self.last_usage: dict | None = None

    def parse(self, *, system: str, content: list[dict], schema: type[T], max_tokens: int, effort: str) -> T:
        import anthropic

        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
                output_config={"effort": effort},
                # If the request is declined by a safety classifier, the API
                # retries it on a fallback model inside the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY is missing or invalid. Put it in .env") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"LLM request rejected: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"LLM API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Can't reach the LLM API (network error).") from e

        usage = response.usage
        # read by the latency timeline (model + token counts of the last call)
        self.last_usage = {
            "model": response.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None) or 0,
        }
        if response.stop_reason == "refusal":
            raise LLMError("The LLM declined this request.")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"LLM output was cut off at max_tokens={max_tokens}; raise the limit.")
        if response.parsed_output is None:
            raise LLMError("LLM returned no parsable output.")
        return response.parsed_output


class QwenLLM:
    """Qwen through an OpenAI-compatible endpoint: Alibaba Cloud Model Studio (DashScope),
    a local Ollama server, or OpenRouter. Only the base URL, key and model name change.

    Differences from Claude, handled here so callers see the same `parse()`:
      * images go in as `image_url` data URLs instead of Anthropic `image` blocks;
      * there is no schema-enforced output, so the JSON Schema is put in the system
        prompt, the reply is validated with Pydantic, and one corrective retry is made;
      * there is no `effort` setting, so it's ignored;
      * `max_tokens` is capped at `max_output_tokens` (16000 is above some Qwen limits).
    """

    name = "Qwen"

    def __init__(self, model: str, base_url: str, api_key: str | None, *,
                 max_output_tokens: int = 8192, json_mode: bool = True, client=None):
        import openai

        self.model = model
        self.base_url = base_url
        self.max_output_tokens = max_output_tokens
        self.json_mode = json_mode
        # a local Ollama server accepts any key, but the SDK insists on one
        self.client = client or openai.OpenAI(base_url=base_url, api_key=api_key or "none",
                                              timeout=300, max_retries=2)
        self.last_usage: dict | None = None

    def parse(self, *, system: str, content: list[dict], schema: type[T], max_tokens: int, effort: str) -> T:
        system = (f"{system}\n\nReply with a single JSON object and nothing else (no prose, no code "
                  f"fences). It must match this JSON Schema:\n"
                  f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}")
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": [_to_openai_block(b) for b in content]}]
        usage = {"model": self.model, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        self.last_usage = usage
        limit = min(max_tokens, self.max_output_tokens)

        for attempt in range(2):
            choice = self._create(messages, limit, usage)
            text = choice.message.content or ""
            if choice.finish_reason == "length":
                raise LLMError(f"LLM output was cut off at max_tokens={limit}; raise QWEN_MAX_TOKENS.")
            if choice.finish_reason == "content_filter":
                raise LLMError("The LLM declined this request (content filter).")
            try:
                return schema.model_validate_json(_extract_json(text))
            except (ValidationError, ValueError) as e:
                if attempt == 1:
                    raise LLMError(f"LLM reply didn't match the expected format: {str(e)[:300]}") from e
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"That reply was not valid: {str(e)[:1500]}\n"
                                                         "Send the corrected JSON object only."}]
        raise AssertionError("unreachable")

    def _create(self, messages: list[dict], max_tokens: int, usage: dict):
        import openai

        kwargs = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if self.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            try:
                response = self.client.chat.completions.create(**kwargs)
            except openai.BadRequestError as e:
                # some models (or servers) don't support JSON mode; the prompt still asks for JSON
                if "response_format" not in kwargs or "response_format" not in str(e).lower():
                    raise
                self.json_mode = False
                kwargs.pop("response_format")
                response = self.client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as e:
            raise LLMError("QWEN_API_KEY is missing or invalid. Put it in .env") from e
        except openai.BadRequestError as e:
            raise LLMError(f"LLM request rejected: {e.message}") from e
        except openai.APIStatusError as e:
            raise LLMError(f"LLM API error {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"Can't reach the LLM API at {self.base_url} (network error).") from e

        usage["model"] = response.model or self.model
        if response.usage:
            usage["input_tokens"] += response.usage.prompt_tokens or 0
            usage["output_tokens"] += response.usage.completion_tokens or 0
        if not response.choices:
            raise LLMError("LLM returned no choices.")
        return response.choices[0]


def _to_openai_block(block: dict) -> dict:
    """Anthropic-style content block -> OpenAI chat content part."""
    if block.get("type") == "image":
        src = block["source"]
        return {"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}}
    return block  # {"type": "text", "text": ...} is the same in both


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a reply that may have thinking tags, code fences or prose."""
    text = _THINK.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in the reply")
    return text[start:end + 1]


def make_llm(settings: "Settings") -> LLMClient:
    """The LLM chosen by LLM_PROVIDER. Only Claude is enabled right now.

    To use Qwen again: uncomment the two lines below and set LLM_PROVIDER=qwen in .env.
    """
    provider = settings.llm_provider.lower()
    if provider == "claude":
        return AnthropicLLM(settings.llm_model)
    # if provider == "qwen":
    #     return QwenLLM(settings.qwen_model, settings.qwen_base_url, settings.qwen_api_key,
    #                    max_output_tokens=settings.qwen_max_tokens, json_mode=settings.qwen_json_mode)
    raise LLMError(f"Unknown or disabled LLM_PROVIDER {settings.llm_provider!r}; use 'claude'.")
