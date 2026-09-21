"""Per-call price table + the Redis-backed daily cost ledger (`CostLedgerPort`).

`AssistantService` checks `over_cap()` before doing any provider work at all — over cap
means the pipeline answers the "quota" template with zero further calls (plan section 6 /
milestone M6). The prices below are estimates from the plan; log actual numbers once real
traffic exists (M6) and update this table, do not trust it as ground truth forever.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, cast
from zoneinfo import ZoneInfo

# USD per 1M tokens, DeepSeek `deepseek-flash` peak rates — log actual numbers in M6.
DEEPSEEK_INPUT_MISS_PER_1M_USD = 0.30
DEEPSEEK_INPUT_HIT_PER_1M_USD = 0.006
DEEPSEEK_OUTPUT_PER_1M_USD = 1.20

# Flat per-call estimates — log actual numbers in M6.
TYPESAFE_PER_CALL_USD = 0.00004
TAVILY_PER_CALL_USD = 0.0
GEMINI_IMAGE_PER_CALL_USD = 0.04
SERPAPI_PER_CALL_USD = 0.015

_PARIS = ZoneInfo("Europe/Paris")


def deepseek_cost_usd(usage: Any) -> float:
    """Cost of one DeepSeek call from its OpenAI-compatible `usage` object.

    DeepSeek reports `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` in addition to
    the standard `prompt_tokens`/`completion_tokens`; fall back to treating the whole
    prompt as a cache miss when those fields are absent (e.g. a stubbed/older response).
    """
    hit = getattr(usage, "prompt_cache_hit_tokens", None) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    if miss is None:
        miss = max((getattr(usage, "prompt_tokens", None) or 0) - hit, 0)
    completion = getattr(usage, "completion_tokens", None) or 0
    return (
        hit / 1_000_000 * DEEPSEEK_INPUT_HIT_PER_1M_USD
        + miss / 1_000_000 * DEEPSEEK_INPUT_MISS_PER_1M_USD
        + completion / 1_000_000 * DEEPSEEK_OUTPUT_PER_1M_USD
    )


def _today_key() -> str:
    return f"assistant:cost:{datetime.now(_PARIS).strftime('%Y-%m-%d')}"


class RedisCostLedger:
    """Implements CostLedgerPort with a Redis float counter, one key per Paris-local day."""

    #: Kept well past a day so a slow job that straddles midnight can still be read back.
    _TTL_SECONDS = 60 * 60 * 48

    def __init__(self, redis_url: str, daily_cap_usd: float) -> None:
        from redis import Redis

        self._redis = Redis.from_url(redis_url)
        self._cap = daily_cap_usd

    def add(self, kind: str, usd: float) -> None:
        key = _today_key()
        self._redis.incrbyfloat(key, usd)
        self._redis.expire(key, self._TTL_SECONDS)

    def today_total(self) -> float:
        # The sync `Redis` client's `.get()` is typed to also cover the async client
        # (`Awaitable[...] | ...`); this instance is always built via `Redis.from_url`,
        # never `aioredis`, so the result is always the plain bytes/str/None case.
        value = cast(Optional[bytes], self._redis.get(_today_key()))
        return float(value) if value is not None else 0.0

    def over_cap(self) -> bool:
        return self.today_total() >= self._cap


class InMemoryCostLedger:
    """Test/dev CostLedgerPort — no Redis, resets on process restart."""

    def __init__(self, daily_cap_usd: float = 5.0) -> None:
        self._cap = daily_cap_usd
        self._totals: dict[str, float] = {}
        self._day: Optional[str] = None

    def _today(self) -> str:
        return datetime.now(_PARIS).strftime("%Y-%m-%d")

    def add(self, kind: str, usd: float) -> None:
        today = self._today()
        self._totals[today] = self._totals.get(today, 0.0) + usd

    def today_total(self) -> float:
        return self._totals.get(self._today(), 0.0)

    def over_cap(self) -> bool:
        return self.today_total() >= self._cap
