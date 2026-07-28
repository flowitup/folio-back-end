"""Unit tests for SQLAlchemyInvoiceRepository.sum_funds_released_split.

Mirrors tests/unit/infrastructure/test_sum_company_spent.py: uses the shared
function-scoped SQLite session from conftest. All IDs are UUID objects to
satisfy SQLite's UUID(as_uuid=True) columns.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel


def _now():
    return datetime.now(timezone.utc)


def _make_user(session) -> UUID:
    user = UserModel(
        id=uuid4(),
        email=f"u{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(user)
    session.flush()
    return user.id


def _make_company(session, user_id: UUID, legal_name: str = "Test Co") -> UUID:
    now = _now()
    company = CompanyModel(
        id=uuid4(),
        legal_name=legal_name,
        address="1 rue",
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(company)
    session.flush()
    return company.id


def _make_project(session, owner_id: UUID, company_id: "UUID | None") -> UUID:
    project = ProjectModel(
        id=uuid4(),
        name=f"P-{uuid4().hex[:6]}",
        owner_id=owner_id,
        company_id=company_id,
    )
    session.add(project)
    session.flush()
    return project.id


def _make_payment_method(
    session,
    company_id: UUID,
    *,
    is_personal_payment: bool = False,
    is_company_payment: bool = False,
    is_active: bool = True,
) -> UUID:
    now = _now()
    pm = PaymentMethodModel(
        id=uuid4(),
        company_id=company_id,
        label=f"PM-{uuid4().hex[:6]}",
        is_builtin=False,
        is_active=is_active,
        is_personal_payment=is_personal_payment,
        is_company_payment=is_company_payment,
        created_by=None,
        created_at=now,
        updated_at=now,
    )
    session.add(pm)
    session.flush()
    return pm.id


def _make_invoice(
    session,
    project_id: UUID,
    inv_type: str,
    amount: float,
    payment_method_id: "UUID | None" = None,
) -> UUID:
    inv = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=f"INV-{uuid4().hex[:8]}",
        type=inv_type,
        issue_date=date.today(),
        recipient_name="Recipient",
        items=[{"description": "Line", "quantity": 1, "unit_price": amount, "vat_rate": 0}],
        payment_method_id=payment_method_id,
    )
    session.add(inv)
    session.flush()
    return inv.id


class TestSumFundsReleasedSplit:
    def test_personal_flagged_method_counts_as_personal(self, session):
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(session, project_id, "released_funds", 300.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == Decimal("0")
        assert personal_total == pytest.approx(Decimal("300.00"), abs=Decimal("0.01"))

    def test_company_flagged_method_counts_as_company(self, session):
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(session, project_id, "released_funds", 400.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == pytest.approx(Decimal("400.00"), abs=Decimal("0.01"))
        assert personal_total == Decimal("0")

    def test_unflagged_method_counts_as_company(self, session):
        """A release paid via a method with neither flag set still counts as company."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id)
        _make_invoice(session, project_id, "released_funds", 150.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == pytest.approx(Decimal("150.00"), abs=Decimal("0.01"))
        assert personal_total == Decimal("0")

    def test_null_payment_method_counts_as_company(self, session):
        """A release with no payment_method_id at all still counts as company."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        _make_invoice(session, project_id, "released_funds", 500.0, payment_method_id=None)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == pytest.approx(Decimal("500.00"), abs=Decimal("0.01"))
        assert personal_total == Decimal("0")

    def test_non_released_funds_invoices_excluded(self, session):
        """Only released_funds invoices are summed — other types are ignored."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(session, project_id, "materials_services", 999.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == Decimal("0")
        assert personal_total == Decimal("0")

    def test_soft_deleted_personal_method_still_counts(self, session):
        """Soft-deleted (is_active=false) personal-payment method still splits as personal."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True, is_active=False)
        _make_invoice(session, project_id, "released_funds", 220.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == Decimal("0")
        assert personal_total == pytest.approx(Decimal("220.00"), abs=Decimal("0.01"))

    def test_invariant_split_sums_to_total(self, session):
        """company_total + personal_total == sum_funds_released for a mixed set."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        personal_pm = _make_payment_method(session, company_id, is_personal_payment=True)
        company_pm = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(session, project_id, "released_funds", 300.0, payment_method_id=personal_pm)
        _make_invoice(session, project_id, "released_funds", 400.0, payment_method_id=company_pm)
        _make_invoice(session, project_id, "released_funds", 100.0, payment_method_id=None)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)
        grand_total = repo.sum_funds_released(project_id)

        assert company_total == pytest.approx(Decimal("500.00"), abs=Decimal("0.01"))
        assert personal_total == pytest.approx(Decimal("300.00"), abs=Decimal("0.01"))
        assert company_total + personal_total == pytest.approx(grand_total, abs=Decimal("0.01"))

    def test_empty_project_returns_zero_zero(self, session):
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == Decimal("0")
        assert personal_total == Decimal("0")

    def test_project_without_company_returns_all_company(self, session):
        """Project with no company has no personal-flagged methods — everything is company."""
        user_id = _make_user(session)
        project_id = _make_project(session, user_id, None)
        _make_invoice(session, project_id, "released_funds", 100.0)

        repo = SQLAlchemyInvoiceRepository(session)
        company_total, personal_total = repo.sum_funds_released_split(project_id)

        assert company_total == pytest.approx(Decimal("100.00"), abs=Decimal("0.01"))
        assert personal_total == Decimal("0")
