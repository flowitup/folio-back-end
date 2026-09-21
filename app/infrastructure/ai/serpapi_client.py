"""SerpApi Google Lens adapter — implements `LensPort` (feature A fallback, phase 04).

`identify()` needs a PUBLIC image URL: Google Lens fetches the image itself from
`url=`, it cannot be handed raw bytes. Chat photos sit in private S3 storage, so the
feature layer decides whether/how to mint a public URL and simply skips Lens (never
raises) when it cannot — this adapter only wraps the HTTP call.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.application.assistant.exceptions import LlmOutputError, ProviderNotConfiguredError
from app.application.assistant.ports import CostLedgerPort
from app.infrastructure.ai.cost import SERPAPI_PER_CALL_USD

SERPAPI_URL = "https://serpapi.com/search.json"
_TIMEOUT_SECONDS = 20.0


class SerpApiLens:
    """Implements LensPort against SerpApi's `engine=google_lens`."""

    def __init__(self, api_key: str, cost_ledger: CostLedgerPort) -> None:
        self._api_key = api_key
        self._cost_ledger = cost_ledger

    def identify(self, image_url: str) -> list[dict[str, Any]]:
        try:
            response = httpx.get(
                SERPAPI_URL,
                params={"engine": "google_lens", "url": image_url, "api_key": self._api_key},
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Never interpolate `exc` itself: `httpx.HTTPStatusError`'s message embeds
            # the full request URL, including `api_key=...` — log/raise the exception
            # class + status code only (review finding MEDIUM 1; this path is unreached
            # today since feature A never calls Lens, but wire it safely from the start).
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            raise LlmOutputError(
                f"SerpApi Google Lens request failed: {exc.__class__.__name__} (status={status_code})"
            ) from exc
        self._cost_ledger.add("serpapi", SERPAPI_PER_CALL_USD)
        body = response.json()
        visual_matches: list[dict[str, Any]] = body.get("visual_matches", [])
        return visual_matches


class NullLensPort:
    """LensPort stand-in when SERPAPI_API_KEY is not configured."""

    def identify(self, image_url: str) -> list[dict[str, Any]]:
        raise ProviderNotConfiguredError("SERPAPI_API_KEY is not configured.")
