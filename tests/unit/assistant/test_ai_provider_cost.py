"""Every paid provider adapter must record its own call against the cost ledger (review
finding H1: previously only `DeepSeekVisionLlm` ever called `.add()`, so the daily cost
cap and `today_total()` silently undercounted the actual spend — Gemini image generation
in particular, the dominant per-ticket cost in `SCAN_MODE=genai`, was completely free-
riding). Each adapter takes its real client as an optional constructor argument, so these
tests inject a stub instead of touching the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from google.genai import errors as genai_errors

from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.ports import ChoiceQuestion
from app.infrastructure.ai.cost import GEMINI_IMAGE_PER_CALL_USD, TYPESAFE_PER_CALL_USD, InMemoryCostLedger
from app.infrastructure.ai.deepseek_client import DeepSeekVisionLlm
from app.infrastructure.ai.gemini_client import GeminiImageGen
from app.infrastructure.ai.jev_client import JevDecisionPort


def _fake_jpeg_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_jev_decision_port_records_one_call_per_decide() -> None:
    ledger = InMemoryCostLedger()
    fake_client = MagicMock()
    fake_client.system_one.return_value = SimpleNamespace(
        choices={"pick": SimpleNamespace(choice="a", confidence=0.9, probabilities={"a": 0.9})}, nouls={}
    )
    port = JevDecisionPort(api_key="x", cost_ledger=ledger, client=fake_client)

    port.decide({}, {"pick": ChoiceQuestion(instructions="i", criteria={"a": "a"})})

    assert ledger.by_kind()["jev"] == TYPESAFE_PER_CALL_USD


def test_gemini_image_gen_records_one_call_on_success() -> None:
    ledger = InMemoryCostLedger()
    fake_part = SimpleNamespace(inline_data=SimpleNamespace(data=b"fake-image"))
    fake_content = SimpleNamespace(parts=[fake_part])
    fake_candidate = SimpleNamespace(content=fake_content)
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = SimpleNamespace(candidates=[fake_candidate])
    port = GeminiImageGen(api_key="x", cost_ledger=ledger, client=fake_client)

    result = port.generate(_fake_jpeg_bytes(), "prompt")

    assert result == b"fake-image"
    assert ledger.by_kind()["gemini"] == GEMINI_IMAGE_PER_CALL_USD


def test_gemini_image_gen_still_records_the_call_even_when_no_image_part_comes_back() -> None:
    """The call itself was billed by Gemini regardless of the response shape — the
    ledger must reflect that even on the `LlmOutputError` path."""
    ledger = InMemoryCostLedger()
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = SimpleNamespace(candidates=[])
    port = GeminiImageGen(api_key="x", cost_ledger=ledger, client=fake_client)

    with pytest.raises(LlmOutputError):
        port.generate(_fake_jpeg_bytes(), "prompt")

    assert ledger.by_kind()["gemini"] == GEMINI_IMAGE_PER_CALL_USD


def test_gemini_image_gen_converts_an_sdk_error_to_llm_output_error() -> None:
    """A transient Gemini 429/5xx was previously unhandled here, so it propagated as a
    raw `google.genai` exception no caller catches — crashing the whole ticket import
    instead of falling back to OpenCV (`TicketFeature._make_scan` only ever catches
    `LlmOutputError`)."""
    ledger = InMemoryCostLedger()
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = genai_errors.ClientError(
        code=429, response_json={"error": {"message": "rate limited"}}
    )
    port = GeminiImageGen(api_key="x", cost_ledger=ledger, client=fake_client)

    with pytest.raises(LlmOutputError):
        port.generate(_fake_jpeg_bytes(), "prompt")

    # The call itself failed — nothing was billed for it.
    assert ledger.today_total() == 0.0


def test_gemini_image_gen_client_uses_an_explicit_request_timeout() -> None:
    """A hung Gemini call must not eat the RQ worker's whole job_timeout."""
    with patch("app.infrastructure.ai.gemini_client.genai.Client") as client_cls:
        GeminiImageGen(api_key="x", cost_ledger=InMemoryCostLedger())

    _args, kwargs = client_cls.call_args
    assert kwargs["http_options"].timeout is not None
    assert kwargs["http_options"].timeout <= 120_000


def test_deepseek_vision_llm_converts_an_sdk_error_to_llm_output_error() -> None:
    """DeepSeek's `openai.APIError` was previously unhandled here."""
    ledger = InMemoryCostLedger()
    fake_client = MagicMock()
    request = httpx.Request("POST", "https://api.deepseek.com")
    fake_client.chat.completions.create.side_effect = openai.APIConnectionError(request=request)
    port = DeepSeekVisionLlm(api_key="x", cost_ledger=ledger, client=fake_client)

    with pytest.raises(LlmOutputError):
        port.chat_text("system", "hello")

    assert ledger.today_total() == 0.0


def test_deepseek_vision_llm_client_uses_an_explicit_request_timeout() -> None:
    """A hung DeepSeek call must not eat the RQ worker's whole job_timeout."""
    with patch("app.infrastructure.ai.deepseek_client.OpenAI") as openai_cls:
        DeepSeekVisionLlm(api_key="x", cost_ledger=InMemoryCostLedger())

    _args, kwargs = openai_cls.call_args
    assert kwargs["timeout"] is not None
    assert kwargs["timeout"] <= 120
