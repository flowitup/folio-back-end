"""Every paid provider adapter must record its own call against the cost ledger (review
finding H1: previously only `DeepSeekVisionLlm` ever called `.add()`, so the daily cost
cap and `today_total()` silently undercounted the actual spend — Gemini image generation
in particular, the dominant per-ticket cost in `SCAN_MODE=genai`, was completely free-
riding). Each adapter takes its real client as an optional constructor argument, so these
tests inject a stub instead of touching the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.ports import ChoiceQuestion
from app.infrastructure.ai.cost import GEMINI_IMAGE_PER_CALL_USD, TYPESAFE_PER_CALL_USD, InMemoryCostLedger
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
