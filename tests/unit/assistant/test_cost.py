"""Unit tests for `app.infrastructure.ai.cost` — price math + the cost-cap gate.

`RedisCostLedger` needs a real Redis; only `deepseek_cost_usd` (pure function) is
exercised against it here. The cap behaviour itself (`over_cap`) is exercised through
`InMemoryCostLedger`, which is the same `CostLedgerPort` contract with no I/O — this is
the "cost-cap path" test the phase asks for (`AssistantService` calls `over_cap()` before
any provider work, see `tests/unit/assistant/test_service.py`).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.infrastructure.ai.cost import (
    DEEPSEEK_INPUT_HIT_PER_1M_USD,
    DEEPSEEK_INPUT_MISS_PER_1M_USD,
    DEEPSEEK_OUTPUT_PER_1M_USD,
    InMemoryCostLedger,
    deepseek_cost_usd,
)


def test_deepseek_cost_usd_with_explicit_cache_fields() -> None:
    usage = SimpleNamespace(prompt_cache_hit_tokens=1_000_000, prompt_cache_miss_tokens=0, completion_tokens=0)
    assert deepseek_cost_usd(usage) == DEEPSEEK_INPUT_HIT_PER_1M_USD


def test_deepseek_cost_usd_falls_back_to_prompt_tokens_when_cache_fields_absent() -> None:
    # No prompt_cache_hit_tokens/prompt_cache_miss_tokens at all -> whole prompt is a miss.
    usage = SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    expected = DEEPSEEK_INPUT_MISS_PER_1M_USD + DEEPSEEK_OUTPUT_PER_1M_USD
    assert deepseek_cost_usd(usage) == expected


def test_deepseek_cost_usd_mixed_hit_and_miss() -> None:
    usage = SimpleNamespace(prompt_cache_hit_tokens=500_000, prompt_cache_miss_tokens=500_000, completion_tokens=0)
    expected = 0.5 * DEEPSEEK_INPUT_HIT_PER_1M_USD + 0.5 * DEEPSEEK_INPUT_MISS_PER_1M_USD
    assert deepseek_cost_usd(usage) == expected


def test_in_memory_cost_ledger_accumulates_and_caps() -> None:
    ledger = InMemoryCostLedger(daily_cap_usd=1.0)
    assert ledger.today_total() == 0.0
    assert ledger.over_cap() is False

    ledger.add("deepseek", 0.4)
    ledger.add("typesafe", 0.00004)
    assert ledger.today_total() == 0.40004
    assert ledger.over_cap() is False

    ledger.add("gemini", 0.6)
    assert ledger.over_cap() is True


def test_in_memory_cost_ledger_defaults_to_five_dollar_cap() -> None:
    ledger = InMemoryCostLedger()
    ledger.add("deepseek", 4.99)
    assert ledger.over_cap() is False
    ledger.add("deepseek", 0.02)
    assert ledger.over_cap() is True


def test_in_memory_cost_ledger_by_kind_breaks_down_every_provider() -> None:
    """`scripts/assistant_costs.py`'s per-kind breakdown for the owner — every provider
    that spends money records against its own `kind` (phase 05 hardening: previously
    only DeepSeek ever called `.add()`, so `today_total()` undercounted real spend)."""
    ledger = InMemoryCostLedger(daily_cap_usd=5.0)
    ledger.add("deepseek_vision", 0.05)
    ledger.add("deepseek_text", 0.01)
    ledger.add("jev", 0.00004)
    ledger.add("gemini", 0.04)
    ledger.add("deepseek_browser", 0.02)

    by_kind = ledger.by_kind()

    assert by_kind == {
        "deepseek_vision": 0.05,
        "deepseek_text": 0.01,
        "jev": 0.00004,
        "gemini": 0.04,
        "deepseek_browser": 0.02,
    }
    assert ledger.today_total() == sum(by_kind.values())


def test_in_memory_cost_ledger_by_kind_defaults_unseen_kinds_to_zero() -> None:
    ledger = InMemoryCostLedger()
    ledger.add("gemini", 0.04)
    by_kind = ledger.by_kind()
    assert by_kind["gemini"] == 0.04
    assert by_kind["jev"] == 0.0
    assert by_kind["deepseek_browser"] == 0.0
