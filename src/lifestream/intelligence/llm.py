"""Thin LLM wrapper so the pipeline can swap providers or use a fake in tests."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

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
