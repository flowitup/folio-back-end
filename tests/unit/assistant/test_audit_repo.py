"""Unit tests for `SqlAlchemyAssistantAuditRepository` against the in-memory SQLite DB
(the `session` fixture) — round-trip and `list_for_company` filtering (D17 layer 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.infrastructure.database.repositories.sqlalchemy_assistant_audit_repository import (
    SqlAlchemyAssistantAuditRepository,
)


def _repo(session) -> SqlAlchemyAssistantAuditRepository:
    return SqlAlchemyAssistantAuditRepository(session)


def test_add_then_list_for_company_round_trips_every_field(session) -> None:
    repo = _repo(session)
    company_id = uuid4()
    user_id = uuid4()
    message_id = uuid4()

    entry = repo.add(
        company_id=company_id,
        channel_key=f"company:{company_id}",
        user_id=user_id,
        message_id=message_id,
        intent="find_equipment",
        feature="equipment",
        tools={"queried": "inventory"},
        outcome="replied",
        refused_reason=None,
        cost_usd=Decimal("0.01234"),
        trace_id="abcd1234",
    )

    assert entry.company_id == company_id
    assert entry.channel_key == f"company:{company_id}"
    assert entry.user_id == user_id
    assert entry.message_id == message_id
    assert entry.intent == "find_equipment"
    assert entry.feature == "equipment"
    assert entry.tools == {"queried": "inventory"}
    assert entry.outcome == "replied"
    assert entry.refused_reason is None
    assert entry.cost_usd == Decimal("0.01234")
    assert entry.trace_id == "abcd1234"
    assert entry.created_at is not None

    rows = repo.list_for_company(company_id)
    assert len(rows) == 1
    assert rows[0].id == entry.id


def test_add_defaults_cost_and_optional_fields(session) -> None:
    repo = _repo(session)
    company_id = uuid4()

    entry = repo.add(
        company_id=company_id,
        channel_key=f"admin:{company_id}",
        user_id=None,
        message_id=None,
        intent=None,
        feature=None,
        outcome="refused",
        refused_reason="finance_company",
    )

    assert entry.user_id is None
    assert entry.message_id is None
    assert entry.tools is None
    assert entry.cost_usd == Decimal("0")
    assert entry.refused_reason == "finance_company"


def test_list_for_company_scopes_to_the_company_and_newest_first(session) -> None:
    repo = _repo(session)
    company_a = uuid4()
    company_b = uuid4()

    first = repo.add(
        company_id=company_a,
        channel_key="company:a",
        user_id=None,
        message_id=None,
        intent=None,
        feature=None,
        outcome="replied",
    )
    second = repo.add(
        company_id=company_a,
        channel_key="company:a",
        user_id=None,
        message_id=None,
        intent=None,
        feature=None,
        outcome="replied",
    )
    repo.add(
        company_id=company_b,
        channel_key="company:b",
        user_id=None,
        message_id=None,
        intent=None,
        feature=None,
        outcome="replied",
    )

    rows = repo.list_for_company(company_a)
    assert [r.id for r in rows] == [second.id, first.id]


def test_list_for_company_filters_by_date_range_and_user(session) -> None:
    repo = _repo(session)
    company_id = uuid4()
    asker = uuid4()
    other_user = uuid4()

    in_range = repo.add(
        company_id=company_id,
        channel_key="admin:x",
        user_id=asker,
        message_id=None,
        intent=None,
        feature=None,
        outcome="replied",
    )
    repo.add(
        company_id=company_id,
        channel_key="admin:x",
        user_id=other_user,
        message_id=None,
        intent=None,
        feature=None,
        outcome="replied",
    )

    now = datetime.now(timezone.utc)
    rows = repo.list_for_company(company_id, from_=now - timedelta(hours=1), to=now + timedelta(hours=1), user_id=asker)
    assert [r.id for r in rows] == [in_range.id]

    assert repo.list_for_company(company_id, from_=now + timedelta(hours=1)) == []
