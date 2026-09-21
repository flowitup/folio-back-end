"""Allowed-domain enforcement (plan hard rule 3: browser agent restricted to merchant
sites) — unit-testable without a live browser."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.application.assistant.jobs_repo import AssistantJobRecord
from app.infrastructure.browser_worker.merchants import (
    MERCHANT_DOMAINS,
    build_allowed_domains,
    build_task,
    is_allowed,
)


class TestBuildAllowedDomains:
    def test_contains_only_the_seven_merchant_wildcard_patterns(self) -> None:
        domains = build_allowed_domains()
        assert len(domains) == 7
        assert set(domains) == {f"*.{domain}" for domain in MERCHANT_DOMAINS.values()}
        assert all(pattern.startswith("*.") for pattern in domains)


class TestIsAllowed:
    def test_merchant_domain_allowed(self) -> None:
        assert is_allowed("https://www.leroymerlin.fr/compte/mes-achats") is True

    def test_merchant_subdomain_allowed(self) -> None:
        assert is_allowed("https://compte.pointp.fr/factures") is True

    def test_google_is_not_allowed(self) -> None:
        assert is_allowed("https://www.google.com") is False

    def test_lookalike_domain_is_not_allowed(self) -> None:
        # A domain merely containing a merchant name is not the merchant's own domain.
        assert is_allowed("https://leroymerlin.fr.evil.example") is False

    def test_empty_or_malformed_url_is_not_allowed(self) -> None:
        assert is_allowed("") is False
        assert is_allowed("not a url") is False


class TestBuildTask:
    def _job(self, merchant: str = "leroymerlin") -> AssistantJobRecord:
        now = date(2026, 9, 10)
        return AssistantJobRecord(
            id=uuid4(),
            type="fetch_invoice",
            user_id=uuid4(),
            merchant=merchant,
            amount_ttc=Decimal("79.54"),
            date=now,
            project_hint=None,
            status="queued",
            attempts=0,
            run_after=None,  # type: ignore[arg-type] - unused by build_task
            result=None,
            pdf_storage_key=None,
            status_message_id=None,
            lang=None,
            processed_at=None,
            created_at=None,  # type: ignore[arg-type]
            updated_at=None,  # type: ignore[arg-type]
        )

    def test_includes_date_and_amount(self) -> None:
        task = build_task(self._job())
        assert "10/09/2026" in task
        assert "79.54" in task

    def test_includes_known_account_url(self) -> None:
        task = build_task(self._job(merchant="leroymerlin"))
        assert "leroymerlin.fr/compte/mes-achats" in task

    def test_mentions_not_ready_and_blocked_statuses(self) -> None:
        task = build_task(self._job())
        assert "not_ready" in task
        assert "blocked" in task
