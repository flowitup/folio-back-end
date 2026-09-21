"""Unit tests for `app.application.assistant.extract` (S1) — no network.

`extract_invoice` itself is thin (it just calls `VisionLlmPort.chat_json` with the fixed
system prompt); the interesting behaviour — pydantic validation + one retry at
temperature 0.2 — lives in `DeepSeekVisionLlm.chat_json` (`app.infrastructure.ai`), so the
"retry-then-fail" path here is exercised at the port boundary: `ScriptedVision` raises
`LlmOutputError` exactly the way the real adapter does once its own retry is exhausted,
and this module must let that propagate rather than swallow it.
"""

from __future__ import annotations

import io

import img2pdf
import shutil

import pytest
from PIL import Image

from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.extract import S1_SYSTEM_PROMPT_FR, extract_invoice, is_readable, pdf_to_images
from app.application.assistant.gate import READABILITY_MIN
from app.application.assistant.models import Invoice, Line
from tests.fakes.ai import ScriptedVision


def _sample_invoice(readability: float = 0.95) -> Invoice:
    return Invoice(
        merchant="Leroy Merlin",
        store_city="Arcueil",
        invoice_number="LM-123",
        date="2026-09-21",
        reference_field=None,
        delivery_address=None,
        total_ht=66.28,
        total_tva=13.26,
        total_ttc=79.54,
        tva_rates=[20.0],
        lines=[Line(label="Ciment 25kg", qty=2, unit_price_ht=33.14, total_ttc=39.77)],
        payment_method="carte",
        readability=readability,
    )


def test_extract_invoice_calls_chat_json_with_the_fixed_system_prompt_and_model() -> None:
    vision = ScriptedVision(json_answers=[_sample_invoice()])
    images = [b"fake-jpeg-bytes"]

    result = extract_invoice(vision, images)

    assert isinstance(result, Invoice)
    assert result.merchant == "Leroy Merlin"
    assert len(vision.json_calls) == 1
    system, user_text, n_images = vision.json_calls[0]
    assert system == S1_SYSTEM_PROMPT_FR
    assert "facture" in user_text.lower() or "extrait" in user_text.lower()
    assert n_images == 1


def test_extract_invoice_propagates_llm_output_error_after_the_ports_own_retry() -> None:
    # ScriptedVision.raise_llm_output_error simulates DeepSeekVisionLlm having already
    # retried once at temperature=0.2 and failed again — extract.py must not swallow it.
    vision = ScriptedVision(raise_llm_output_error=True)

    with pytest.raises(LlmOutputError):
        extract_invoice(vision, [b"illegible-photo"])


def test_is_readable_threshold() -> None:
    assert is_readable(_sample_invoice(readability=READABILITY_MIN)) is True
    assert is_readable(_sample_invoice(readability=READABILITY_MIN - 0.01)) is False


@pytest.mark.skipif(shutil.which("pdfinfo") is None, reason="poppler-utils (pdfinfo) not installed")
def test_pdf_to_images_renders_at_least_one_jpeg_page() -> None:
    # A real single-page PDF built from a tiny in-memory image — exercises the real
    # pdf2image/poppler round trip, not a mock.
    page = Image.new("RGB", (40, 60), color="white")
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    pdf_bytes = img2pdf.convert(buffer.getvalue())

    images = pdf_to_images(pdf_bytes)

    assert len(images) == 1
    assert images[0][:2] == b"\xff\xd8"  # JPEG magic bytes
