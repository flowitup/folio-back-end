"""Unit tests for `app.infrastructure.browser_worker.agent` — the browser agent's own
cost accounting (review finding NEW-H4). `browser-use` is not installed in the normal
test environment (see the module docstring), so `run_fetch`'s billing wiring is proven
here with a fake `browser_use` module injected into `sys.modules`, not the real package;
`_browser_agent_cost_usd` (the pure extraction logic) is tested directly against fake
history objects shaped like `browser_use.agent.views.AgentHistoryList`.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

import pytest

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.infrastructure.ai.cost import BROWSER_AGENT_STEP_ESTIMATE_USD, InMemoryCostLedger
from app.infrastructure.browser_worker.agent import COST_KIND, _browser_agent_cost_usd, run_fetch


class _FakeUsage:
    def __init__(self, total_cost: Optional[float]) -> None:
        self.total_cost = total_cost


class _FakeHistory:
    def __init__(self, *, total_cost: Optional[float], usage_present: bool = True, steps: int = 4) -> None:
        self.usage = _FakeUsage(total_cost) if usage_present else None
        self._steps = steps

    def __len__(self) -> int:
        return self._steps

    def final_result(self) -> Optional[str]:
        return None


class TestBrowserAgentCostUsd:
    def test_prefers_browser_use_own_accounting(self) -> None:
        history = _FakeHistory(total_cost=0.1234)
        assert _browser_agent_cost_usd(history) == 0.1234

    def test_falls_back_to_step_estimate_when_usage_is_none(self) -> None:
        history = _FakeHistory(total_cost=None, usage_present=False, steps=5)
        assert _browser_agent_cost_usd(history) == pytest.approx(5 * BROWSER_AGENT_STEP_ESTIMATE_USD)

    def test_falls_back_to_step_estimate_when_total_cost_is_zero(self) -> None:
        # A reported $0 is indistinguishable from "pricing lookup failed" (see the
        # function's own docstring) — treated as unavailable rather than trusted.
        history = _FakeHistory(total_cost=0.0, steps=3)
        assert _browser_agent_cost_usd(history) == pytest.approx(3 * BROWSER_AGENT_STEP_ESTIMATE_USD)

    def test_falls_back_to_zero_when_history_has_no_length(self) -> None:
        history = object()
        assert _browser_agent_cost_usd(history) == 0.0


def _job() -> AssistantJobRecord:
    return AssistantJobRecord(
        id=uuid4(),
        type="fetch_invoice",
        user_id=uuid4(),
        merchant="leroymerlin",
        amount_ttc=Decimal("10.00"),
        date=date(2026, 9, 10),
        project_hint=None,
        status="running",
        attempts=0,
        run_after=None,  # type: ignore[arg-type]
        result=None,
        pdf_storage_key=None,
        status_message_id=None,
        lang=None,
        processed_at=None,
        created_at=None,  # type: ignore[arg-type]
        updated_at=None,  # type: ignore[arg-type]
    )


def _install_fake_browser_use(monkeypatch: pytest.MonkeyPatch, history: Any) -> None:
    """Injects a minimal fake `browser_use` module so `run_fetch`'s lazy `from
    browser_use import Agent, Browser, ChatOpenAI` succeeds without the real (heavy,
    image-only) dependency installed."""

    class _FakeBrowser:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    class _FakeChatOpenAI:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    class _FakeAgent:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run(self, max_steps: int) -> Any:
            return history

    fake_module = types.SimpleNamespace(Agent=_FakeAgent, Browser=_FakeBrowser, ChatOpenAI=_FakeChatOpenAI)
    monkeypatch.setitem(sys.modules, "browser_use", fake_module)


class TestRunFetchBillsTheLedger:
    def test_bills_the_ledger_after_a_successful_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        history = _FakeHistory(total_cost=0.42)
        _install_fake_browser_use(monkeypatch, history)
        ledger = InMemoryCostLedger()

        outcome = asyncio.run(
            run_fetch(
                _job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
                cost_ledger=ledger,
            )
        )

        assert ledger.by_kind()[COST_KIND] == pytest.approx(0.42)
        # Billing must not depend on (or block) the outcome being classified "done".
        assert outcome.result.status == "failed"

    def test_a_missing_ledger_never_blocks_the_fetch(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        history = _FakeHistory(total_cost=0.1)
        _install_fake_browser_use(monkeypatch, history)

        outcome = asyncio.run(
            run_fetch(
                _job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
                cost_ledger=None,
            )
        )
        assert outcome.result.status == "failed"  # no PDF, no structured result — but no crash either

    def test_a_ledger_write_failure_never_blocks_the_fetch(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        history = _FakeHistory(total_cost=0.1)
        _install_fake_browser_use(monkeypatch, history)

        class _RaisingLedger:
            def add(self, kind: str, usd: float) -> None:
                raise RuntimeError("redis is down")

        outcome = asyncio.run(
            run_fetch(
                _job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
                cost_ledger=_RaisingLedger(),  # type: ignore[arg-type]
            )
        )
        assert outcome.result.status == "failed"
