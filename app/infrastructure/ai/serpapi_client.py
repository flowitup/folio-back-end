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

SERPAPI_URL = "https://serpapi.com/search.json"
_TIMEOUT_SECONDS = 20.0


class SerpApiLens:
    """Implements LensPort against SerpApi's `engine=google_lens`."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def identify(self, image_url: str) -> list[dict[str, Any]]:
        try:
            response = httpx.get(
                SERPAPI_URL,
                params={"engine": "google_lens", "url": image_url, "api_key": self._api_key},
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmOutputError(f"SerpApi Google Lens request failed: {exc}") from exc
        body = response.json()
        visual_matches: list[dict[str, Any]] = body.get("visual_matches", [])
        return visual_matches


class NullLensPort:
    """LensPort stand-in when SERPAPI_API_KEY is not configured."""

    def identify(self, image_url: str) -> list[dict[str, Any]]:
        raise ProviderNotConfiguredError("SERPAPI_API_KEY is not configured.")
