"""Unit tests for `app.infrastructure.ai.deepseek_client` — provider-outage errors map
to a distinct exception from a genuinely bad/unparseable reply, so callers can tell a
user to retry later instead of asking them to retake a perfectly good photo."""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest

from app.application.assistant.exceptions import LlmOutputError, LlmUnavailableError
from app.infrastructure.ai.deepseek_client import DeepSeekVisionLlm

_REQUEST = httpx.Request("POST", "https://api.deepseek.com/chat/completions")


class FakeCostLedger:
    def add(self, kind: str, usd: float) -> None:
        pass

    def over_cap(self) -> bool:
        return False


class _RaisingCompletions:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create(self, **_kwargs: Any) -> Any:
        raise self._exc


class _RaisingChat:
    def __init__(self, exc: Exception) -> None:
        self.completions = _RaisingCompletions(exc)


class FakeOpenAIClient:
    def __init__(self, exc: Exception) -> None:
        self.chat = _RaisingChat(exc)


def _client_raising(exc: Exception) -> DeepSeekVisionLlm:
    return DeepSeekVisionLlm(api_key="unused", cost_ledger=FakeCostLedger(), client=FakeOpenAIClient(exc))


@pytest.mark.parametrize(
    "exc",
    [
        openai.APIConnectionError(request=_REQUEST),
        openai.APITimeoutError(request=_REQUEST),
        openai.RateLimitError("rate limited", response=httpx.Response(429, request=_REQUEST), body=None),
        openai.InternalServerError("server error", response=httpx.Response(503, request=_REQUEST), body=None),
    ],
)
def test_provider_outage_errors_raise_llm_unavailable_error(exc: Exception) -> None:
    client = _client_raising(exc)
    with pytest.raises(LlmUnavailableError):
        client.chat_text(system="s", user_text="u")


def test_llm_unavailable_error_is_still_caught_by_the_older_llm_output_error_handler() -> None:
    """A caller that has not been updated to the new exception must still catch it —
    `LlmUnavailableError` is a subclass of `LlmOutputError` on purpose."""
    client = _client_raising(openai.APIConnectionError(request=_REQUEST))
    try:
        client.chat_text(system="s", user_text="u")
        raise AssertionError("expected an exception")
    except LlmOutputError:
        pass


def test_a_non_outage_api_error_still_raises_the_plain_llm_output_error() -> None:
    exc = openai.BadRequestError("bad request", response=httpx.Response(400, request=_REQUEST), body=None)
    client = _client_raising(exc)
    with pytest.raises(LlmOutputError) as excinfo:
        client.chat_text(system="s", user_text="u")
    assert not isinstance(excinfo.value, LlmUnavailableError)
