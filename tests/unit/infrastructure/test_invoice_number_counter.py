"""Expense and funds-release numbers come from a persisted counter: a deleted
number is never handed out again, and the counter never falls behind the
numbers already in use (compared as numbers, not strings)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

import app.domain.time as time_module
from app.domain.entities.invoice import InvoiceType
from app.domain.time import business_today
from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from tests.company_tenancy_helper import company_for_projects

YEAR = business_today().year


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def project_and_user(session):
    user = UserModel(
        id=uuid4(), email=f"u{uuid4().hex[:8]}@test.com", is_active=True, created_at=_now(), updated_at=_now()
    )
    session.add(user)
    session.flush()
    project = ProjectModel(
        id=uuid4(), name=f"P-{uuid4().hex[:6]}", owner_id=user.id, company_id=company_for_projects(session, user.id)
    )
    session.add(project)
    session.flush()
    return project.id, user.id


def _insert(session, project_id: UUID, user_id: UUID, number: str) -> InvoiceModel:
    row = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=number,
        type=InvoiceType.OTHERS.value,
        issue_date=date.today(),
        recipient_name="Supplier",
        items=[],
        created_by=user_id,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def test_numbers_follow_each_other(session, project_and_user):
    project_id, _ = project_and_user
    repo = SQLAlchemyInvoiceRepository(session)
    assert repo.next_invoice_number(project_id) == f"INV-{YEAR}-0001"
    assert repo.next_invoice_number(project_id) == f"INV-{YEAR}-0002"


def test_deleting_the_latest_expense_does_not_free_its_number(session, project_and_user):
    project_id, user_id = project_and_user
    repo = SQLAlchemyInvoiceRepository(session)
    first = _insert(session, project_id, user_id, repo.next_invoice_number(project_id))
    latest = _insert(session, project_id, user_id, repo.next_invoice_number(project_id))
    session.delete(latest)
    session.flush()

    assert first.invoice_number == f"INV-{YEAR}-0001"
    assert repo.next_invoice_number(project_id) == f"INV-{YEAR}-0003"


def test_numbers_in_use_before_the_counter_are_continued(session, project_and_user):
    project_id, user_id = project_and_user
    _insert(session, project_id, user_id, f"INV-{YEAR}-0041")
    assert SQLAlchemyInvoiceRepository(session).next_invoice_number(project_id) == f"INV-{YEAR}-0042"


def test_suffixes_are_compared_as_numbers_past_9999(session, project_and_user):
    project_id, user_id = project_and_user
    _insert(session, project_id, user_id, f"INV-{YEAR}-9999")
    _insert(session, project_id, user_id, f"INV-{YEAR}-10000")
    assert SQLAlchemyInvoiceRepository(session).next_invoice_number(project_id) == f"INV-{YEAR}-10001"


def test_funds_release_numbers_have_their_own_monotonic_sequence(session, project_and_user):
    project_id, user_id = project_and_user
    repo = SQLAlchemyInvoiceRepository(session)
    release = _insert(session, project_id, user_id, repo.next_funds_release_number(project_id, year=2025))
    session.delete(release)
    session.flush()

    assert release.invoice_number == "FR-2025-0001"
    assert repo.next_funds_release_number(project_id, year=2025) == "FR-2025-0002"
    assert repo.next_invoice_number(project_id) == f"INV-{YEAR}-0001"


def test_default_number_year_is_the_paris_year_on_new_year_night(session, project_and_user, monkeypatch):
    # 23:30 UTC on 31 December 2026 is already 1 January 2027 in Paris.
    class _NewYearNight(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
            return moment if tz is None else moment.astimezone(tz)

    monkeypatch.setattr(time_module, "datetime", _NewYearNight)
    project_id, _ = project_and_user
    repo = SQLAlchemyInvoiceRepository(session)
    assert repo.next_funds_release_number(project_id) == "FR-2027-0001"
    assert repo.next_invoice_number(project_id) == "INV-2027-0001"
