"""Unit tests for `app.infrastructure.browser_worker.agent` — the browser agent's own
cost accounting (review finding NEW-H4). `browser-use` is not installed in the normal
test environment (see the module docstring), so `run_fetch`'s billing wiring is proven
here with a fake `browser_use` module injected into `sys.modules`, not the real package;
`_browser_agent_cost_usd` (the pure extraction logic) is tested directly against fake
history objects shaped like `browser_use.agent.views.AgentHistoryList`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

import pytest

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.infrastructure.ai.cost import BROWSER_AGENT_STEP_ESTIMATE_USD, InMemoryCostLedger
from app.infrastructure.browser_worker.agent import (
    COST_KIND,
    _browser_agent_cost_usd,
    run_fetch,
    run_product_search,
)


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


def _product_search_job() -> AssistantJobRecord:
    return AssistantJobRecord(
        id=uuid4(),
        type="find_product",
        user_id=uuid4(),
        merchant=None,
        amount_ttc=None,
        date=None,
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
        params={
            "ident": {"name": "Perceuse à percussion", "category": "outillage", "confidence": 0.9},
            "search_queries": ["perceuse bosch 18v"],
            "company_id": str(uuid4()),
            "photo_sha256": "abc123",
            "message_id": str(uuid4()),
            "lang": "fr",
        },
    )


def _install_fake_browser_use(
    monkeypatch: pytest.MonkeyPatch, history: Any, *, captured: Optional[dict[str, Any]] = None
) -> None:
    """Injects a minimal fake `browser_use` module so `run_fetch`'s lazy `from
    browser_use import Agent, Browser, ChatOpenAI, Tools` succeeds without the real
    (heavy, image-only) dependency installed. `captured` (when given) records each fake's
    constructor kwargs so a test can assert on them."""

    class _FakeBrowser:
        def __init__(self, **kwargs: Any) -> None:
            if captured is not None:
                captured["browser_kwargs"] = kwargs

        async def kill(self) -> None:
            if captured is not None:
                captured["browser_killed"] = True

    class _FakeTools:
        def __init__(self, **kwargs: Any) -> None:
            self.exclude_actions = kwargs.get("exclude_actions")
            if captured is not None:
                captured["tools_kwargs"] = kwargs

    class _FakeChatOpenAI:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            if captured is not None:
                captured["agent_kwargs"] = kwargs

        async def run(self, max_steps: int) -> Any:
            return history

    fake_module = types.SimpleNamespace(
        Agent=_FakeAgent, Browser=_FakeBrowser, ChatOpenAI=_FakeChatOpenAI, Tools=_FakeTools
    )
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


class _FakeHistoryWithResult(_FakeHistory):
    def __init__(self, *, raw_result: str, total_cost: Optional[float] = 0.1) -> None:
        super().__init__(total_cost=total_cost)
        self._raw_result = raw_result

    def final_result(self) -> Optional[str]:
        return self._raw_result


class TestRunProductSearch:
    """Owner decision D16: the browser agent also runs feature A's product search —
    mirrors `TestRunFetchBillsTheLedger` above (same lazy import, same billing, same
    never-raises contract) but returns a `ProductSearchResult` directly, with no PDF."""

    def test_returns_the_agents_structured_candidates(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        raw = (
            '{"status": "done", "candidates": [{"url": "https://www.leroymerlin.fr/p/1", '
            '"title": "Perceuse Bosch 18V", "merchant": "leroymerlin"}]}'
        )
        history = _FakeHistoryWithResult(raw_result=raw)
        _install_fake_browser_use(monkeypatch, history)

        result = asyncio.run(
            run_product_search(
                _product_search_job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
            )
        )

        assert result.status == "done"
        assert len(result.candidates) == 1
        assert result.candidates[0].url == "https://www.leroymerlin.fr/p/1"

    def test_bills_the_ledger_after_a_successful_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        history = _FakeHistory(total_cost=0.33)
        _install_fake_browser_use(monkeypatch, history)
        ledger = InMemoryCostLedger()

        result = asyncio.run(
            run_product_search(
                _product_search_job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
                cost_ledger=ledger,
            )
        )

        assert ledger.by_kind()[COST_KIND] == pytest.approx(0.33)
        # No structured output on this fake history -> the safe "failed" fallback, same
        # as run_fetch's own billing-must-not-depend-on-the-outcome test.
        assert result.status == "failed"

    def test_a_missing_ledger_never_blocks_the_search(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        history = _FakeHistory(total_cost=0.1)
        _install_fake_browser_use(monkeypatch, history)

        result = asyncio.run(
            run_product_search(
                _product_search_job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
                cost_ledger=None,
            )
        )
        assert result.status == "failed"  # no structured result — but no crash either

    def test_browser_use_import_failure_is_reported_as_failed(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.delitem(sys.modules, "browser_use", raising=False)
        monkeypatch.setattr(
            "builtins.__import__",
            lambda name, *a, **k: (
                (_ for _ in ()).throw(ImportError("no module")) if name == "browser_use" else __import__(name, *a, **k)
            ),
        )

        result = asyncio.run(
            run_product_search(
                _product_search_job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
            )
        )
        assert result.status == "failed"
        assert "browser-use unavailable" in (result.message or "")


class TestRunFetchExcludesDangerousTools:
    def test_evaluate_and_upload_file_are_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        captured: dict[str, Any] = {}
        _install_fake_browser_use(monkeypatch, _FakeHistory(total_cost=0.1), captured=captured)

        asyncio.run(
            run_fetch(
                _job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
            )
        )

        assert set(captured["tools_kwargs"]["exclude_actions"]) == {"evaluate", "upload_file"}
        assert captured["agent_kwargs"]["tools"] is not None


class TestRunFetchTimesOut:
    def test_a_hung_run_is_reported_failed_and_the_browser_is_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        import app.infrastructure.browser_worker.agent as agent_module

        monkeypatch.setattr(agent_module, "AGENT_RUN_TIMEOUT_SECONDS", 0.05)
        captured: dict[str, Any] = {}

        class _HangingAgent:
            def __init__(self, **kwargs: Any) -> None:
                captured["agent_kwargs"] = kwargs

            async def run(self, max_steps: int) -> Any:
                await asyncio.sleep(10)

        class _FakeBrowser:
            def __init__(self, **kwargs: Any) -> None:
                captured["browser_kwargs"] = kwargs

            async def kill(self) -> None:
                captured["browser_killed"] = True

        fake_module = types.SimpleNamespace(
            Agent=_HangingAgent,
            Browser=_FakeBrowser,
            ChatOpenAI=lambda **_k: None,
            Tools=lambda **k: types.SimpleNamespace(**k),
        )
        monkeypatch.setitem(sys.modules, "browser_use", fake_module)

        outcome = asyncio.run(
            run_fetch(
                _job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=str(tmp_path / "profile"),
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
            )
        )

        assert outcome.result.status == "failed"
        assert "timed out" in (outcome.result.message or "")
        assert captured.get("browser_killed") is True


class TestRunProductSearchUsesAnEphemeralProfile:
    def test_never_reuses_the_merchant_profile_and_cleans_up(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        captured: dict[str, Any] = {}
        _install_fake_browser_use(monkeypatch, _FakeHistory(total_cost=0.1), captured=captured)
        merchant_profile = str(tmp_path / "merchant-profile")

        asyncio.run(
            run_product_search(
                _product_search_job(),
                chrome_path="/usr/bin/google-chrome",
                profile_dir=merchant_profile,
                downloads_dir=str(tmp_path / "downloads"),
                deepseek_api_key="key",
            )
        )

        used_profile = captured["browser_kwargs"]["user_data_dir"]
        assert used_profile != merchant_profile
        assert "folio-product-search-" in used_profile
        assert not os.path.exists(used_profile)  # removed after the run
        assert set(captured["tools_kwargs"]["exclude_actions"]) == {"evaluate", "upload_file"}


class TestClampSearchQueries:
    def test_caps_count_and_length(self) -> None:
        from app.infrastructure.browser_worker.agent import _clamp_search_queries

        queries = ["a" * 200, "b" * 100, "c", "d", "e (should be dropped)"]
        clamped = _clamp_search_queries(queries)
        assert len(clamped) == 3
        assert all(len(q) <= 80 for q in clamped)

    def test_collapses_whitespace_and_drops_blanks(self) -> None:
        from app.infrastructure.browser_worker.agent import _clamp_search_queries

        assert _clamp_search_queries(["  perceuse   bosch  ", "   ", ""]) == ["perceuse bosch"]


class TestIsValidPdf:
    def test_rejects_a_non_pdf_header(self, tmp_path) -> None:
        from app.infrastructure.browser_worker.agent import _is_valid_pdf

        path = tmp_path / "fake.pdf"
        path.write_bytes(b"<html>not a pdf</html>")
        assert _is_valid_pdf(str(path)) is False

    def test_rejects_an_oversized_file(self, tmp_path) -> None:
        from app.infrastructure.browser_worker.agent import MAX_PDF_SIZE_BYTES, _is_valid_pdf

        path = tmp_path / "big.pdf"
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4")
            f.seek(MAX_PDF_SIZE_BYTES + 1)
            f.write(b"\0")
        assert _is_valid_pdf(str(path)) is False

    def test_accepts_a_small_valid_pdf(self, tmp_path) -> None:
        from app.infrastructure.browser_worker.agent import _is_valid_pdf

        path = tmp_path / "ok.pdf"
        path.write_bytes(b"%PDF-1.4 real enough for the header check")
        assert _is_valid_pdf(str(path)) is True
