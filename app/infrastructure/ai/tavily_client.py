"""Tavily adapter — implements `WebSearchPort` (feature A material search, phase 04)."""

from __future__ import annotations

from typing import Any, Optional

from tavily import TavilyClient

from app.application.assistant.exceptions import ProviderNotConfiguredError
from app.application.assistant.ports import CostLedgerPort
from app.infrastructure.ai.cost import TAVILY_PER_CALL_USD


class TavilyWebSearch:
    """Implements WebSearchPort against `tavily.TavilyClient`."""

    def __init__(self, api_key: str, cost_ledger: CostLedgerPort, client: Optional[TavilyClient] = None) -> None:
        self._cost_ledger = cost_ledger
        self._client = client or TavilyClient(api_key)

    def search(
        self,
        query: str,
        *,
        include_domains: list[str] | None = None,
        include_images: bool = True,
        max_results: int = 6,
    ) -> dict[str, Any]:
        result: dict[str, Any] = self._client.search(
            query,
            include_domains=include_domains or [],
            include_images=include_images,
            max_results=max_results,
            search_depth="basic",
        )
        self._cost_ledger.add("tavily", TAVILY_PER_CALL_USD)
        return result

    def extract(self, urls: list[str], *, include_images: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = self._client.extract(urls=urls, include_images=include_images)
        self._cost_ledger.add("tavily", TAVILY_PER_CALL_USD)
        return result


class NullWebSearchPort:
    """WebSearchPort stand-in when TAVILY_API_KEY is not configured."""

    def search(
        self,
        query: str,
        *,
        include_domains: list[str] | None = None,
        include_images: bool = True,
        max_results: int = 6,
    ) -> dict[str, Any]:
        raise ProviderNotConfiguredError("TAVILY_API_KEY is not configured.")

    def extract(self, urls: list[str], *, include_images: bool = True) -> dict[str, Any]:
        raise ProviderNotConfiguredError("TAVILY_API_KEY is not configured.")
