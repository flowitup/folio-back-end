"""DeepSeek (`deepseek-flash`) adapter — implements `VisionLlmPort`.

OpenAI-compatible endpoint (`base_url="https://api.deepseek.com"`). `chat_json` validates
the reply with the given pydantic model and retries exactly once at `temperature=0.2` on
a parse/validation failure (plan hard rule #5), then raises `LlmOutputError`. System
prompts must stay byte-identical across calls to hit DeepSeek's prompt cache (hard rule
#4) — this adapter never mutates or f-strings the `system` argument it is given.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any, Optional, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.application.assistant.exceptions import LlmOutputError, ProviderNotConfiguredError
from app.application.assistant.ports import CostLedgerPort
from app.infrastructure.ai.cost import deepseek_cost_usd

logger = logging.getLogger(__name__)

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
MAX_IMAGE_SIDE_PX = 1600
MAX_TOKENS = 2048
_RETRY_TEMPERATURE = 0.2

T = TypeVar("T", bound=BaseModel)


def _resize_to_jpeg(image_bytes: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as opened:
        rgb_image = opened.convert("RGB")
        width, height = rgb_image.size
        longest = max(width, height)
        if longest > MAX_IMAGE_SIDE_PX:
            scale = MAX_IMAGE_SIDE_PX / longest
            rgb_image = rgb_image.resize((max(1, round(width * scale)), max(1, round(height * scale))))
        buffer = io.BytesIO()
        rgb_image.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()


def _image_data_url(image_bytes: bytes) -> str:
    jpeg_bytes = _resize_to_jpeg(image_bytes)
    encoded = base64.b64encode(jpeg_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _user_content(user_text: str, images: list[bytes]) -> Any:
    if not images:
        return user_text
    parts: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for image_bytes in images:
        parts.append({"type": "image_url", "image_url": {"url": _image_data_url(image_bytes)}})
    return parts


class DeepSeekVisionLlm:
    """Implements VisionLlmPort against DeepSeek's OpenAI-compatible API."""

    def __init__(self, api_key: str, cost_ledger: CostLedgerPort, client: Optional[OpenAI] = None) -> None:
        self._cost_ledger = cost_ledger
        self._client = client or OpenAI(api_key=api_key, base_url=BASE_URL)

    def _complete(self, system: str, user_text: str, images: list[bytes], temperature: float, json_mode: bool) -> str:
        extra: dict[str, Any] = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(
            model=MODEL,
            temperature=temperature,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _user_content(user_text, images)},
            ],
            extra_body={"thinking": {"type": "disabled"}},
            **extra,
        )
        if response.usage is not None:
            self._cost_ledger.add("deepseek", deepseek_cost_usd(response.usage))
        content = response.choices[0].message.content
        return content or ""

    def chat_json(
        self, system: str, user_text: str, images: list[bytes], model_cls: type[T], temperature: float = 0.0
    ) -> T:
        content = self._complete(system, user_text, images, temperature, json_mode=True)
        try:
            return model_cls.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as first_error:
            logger.warning("DeepSeek JSON validation failed at temperature=%.2f, retrying at 0.2", temperature)
            retry_content = self._complete(system, user_text, images, _RETRY_TEMPERATURE, json_mode=True)
            try:
                return model_cls.model_validate_json(retry_content)
            except (ValidationError, json.JSONDecodeError) as second_error:
                raise LlmOutputError(
                    f"DeepSeek output failed validation twice for {model_cls.__name__}: {second_error}"
                ) from first_error

    def chat_text(self, system: str, user_text: str) -> str:
        return self._complete(system, user_text, images=[], temperature=0.3, json_mode=False)


class NullVisionLlm:
    """VisionLlmPort stand-in when DEEPSEEK_API_KEY is not configured."""

    def chat_json(
        self, system: str, user_text: str, images: list[bytes], model_cls: type[T], temperature: float = 0.0
    ) -> T:
        raise ProviderNotConfiguredError("DEEPSEEK_API_KEY is not configured.")

    def chat_text(self, system: str, user_text: str) -> str:
        raise ProviderNotConfiguredError("DEEPSEEK_API_KEY is not configured.")
