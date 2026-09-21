"""Gemini adapter — implements `ImageGenPort` (feature C scan generation, phase 03).

Only wired when `SCAN_MODE=genai`; `SCAN_MODE=opencv` never touches this module. Reads
the generated image back from the first `inline_data` part of the response — Gemini can
also return text parts (e.g. a refusal), which this adapter treats as "no image".
"""

from __future__ import annotations

from typing import Any, Optional

from google import genai

from app.application.assistant.exceptions import LlmOutputError, ProviderNotConfiguredError
from app.application.assistant.ports import CostLedgerPort
from app.infrastructure.ai.cost import GEMINI_IMAGE_PER_CALL_USD

MODEL = "gemini-2.5-flash-image"


class GeminiImageGen:
    """Implements ImageGenPort against `google.genai.Client`."""

    def __init__(self, api_key: str, cost_ledger: CostLedgerPort, client: Optional[genai.Client] = None) -> None:
        self._cost_ledger = cost_ledger
        self._client = client or genai.Client(api_key=api_key)

    def generate(self, image: bytes, prompt: str) -> bytes:
        pil_image = _to_pil_image(image)
        response = self._client.models.generate_content(model=MODEL, contents=[prompt, pil_image])
        self._cost_ledger.add("gemini", GEMINI_IMAGE_PER_CALL_USD)
        for candidate in response.candidates or []:
            if candidate.content is None:
                continue
            for part in candidate.content.parts or []:
                if part.inline_data is not None and part.inline_data.data:
                    return bytes(part.inline_data.data)
        raise LlmOutputError("Gemini returned no image part for the scan-generation prompt.")


def _to_pil_image(image_bytes: bytes) -> Any:
    import io

    from PIL import Image

    return Image.open(io.BytesIO(image_bytes))


class NullImageGenPort:
    """ImageGenPort stand-in when GEMINI_API_KEY is not configured."""

    def generate(self, image: bytes, prompt: str) -> bytes:
        raise ProviderNotConfiguredError("GEMINI_API_KEY is not configured.")
