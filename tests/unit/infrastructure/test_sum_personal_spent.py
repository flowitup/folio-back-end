"""Unit tests for SQLAlchemyInvoiceRepository.sum_personal_spent.

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


def _make_project(session, owner_id: UUID, company_id: UUID) -> UUID:
    project = ProjectModel(
        id=uuid4(),
        name=f"P-{uuid4().hex[:6]}",
        owner_id=owner_id,
        company_id=company_id,
    )
    session.add(project)
    session.flush()
    return project.id


def _make_payment_method(session, company_id: UUID, is_personal_payment: bool, is_active: bool = True) -> UUID:
    now = _now()
    pm = PaymentMethodModel(
        id=uuid4(),
        company_id=company_id,
        label=f"PM-{uuid4().hex[:6]}",
        is_builtin=False,
        is_active=is_active,
        is_personal_payment=is_personal_payment,
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
    refundable_status=None,
    refunded_by=None,
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
        refundable_status=refundable_status,
        refunded_by=refunded_by,
        payment_method_id=payment_method_id,
    )
    session.add(inv)
    session.flush()
    return inv.id


class TestSumPersonalSpent:
    def test_personal_paid_labor_counts(self, session):
        """Labor invoice paid with an is_personal_payment method counts."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(session, project_id, "labor", 500.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == pytest.approx(Decimal("500.00"), abs=Decimal("0.01"))

    def test_personal_paid_ms_counts(self, session):
        """M&S invoice paid with an is_personal_payment method counts."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(session, project_id, "materials_services", 150.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == pytest.approx(Decimal("150.00"), abs=Decimal("0.01"))

    def test_non_personal_method_not_counted(self, session):
        """Invoice paid with a non-personal-flagged method is excluded."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=False)
        _make_invoice(session, project_id, "materials_services", 999.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == Decimal("0")

    def test_no_payment_method_not_counted(self, session):
        """Invoice with no payment_method_id is excluded."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        _make_invoice(session, project_id, "materials_services", 999.0)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == Decimal("0")

    def test_released_funds_never_counted(self, session):
        """released_funds invoices are excluded even when paid via a personal method."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(session, project_id, "released_funds", 10000.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == Decimal("0")

    def test_company_refunded_expense_excluded(self, session):
        """refundable_status='refunded' AND refunded_by != 'bank' — the company
        reimbursed this personal expense, so it is company spend, not personal."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(
            session,
            project_id,
            "materials_services",
            200.0,
            refundable_status="refunded",
            refunded_by="company",
            payment_method_id=pm_id,
        )

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == Decimal("0")

    def test_null_refunded_by_counts_as_company_refunded_and_excluded(self, session):
        """NULL refunded_by on a refunded row is legacy data and counts as
        company-refunded — same convention as sum_company_spent — so it is excluded."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(
            session,
            project_id,
            "materials_services",
            200.0,
            refundable_status="refunded",
            refunded_by=None,
            payment_method_id=pm_id,
        )

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == Decimal("0")

    def test_bank_refunded_expense_still_counts(self, session):
        """refunded_by='bank' — the bank's money, not the company's — so the
        personal expense still counts toward personal spend."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(
            session,
            project_id,
            "materials_services",
            200.0,
            refundable_status="refunded",
            refunded_by="bank",
            payment_method_id=pm_id,
        )

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == pytest.approx(Decimal("200.00"), abs=Decimal("0.01"))

    def test_refund_type_invoice_nets_total_down(self, session):
        """A refund paid via a personal-flagged method (negative lines) nets the total down."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(session, project_id, "materials_services", 100.0, payment_method_id=pm_id)
        _make_invoice(session, project_id, "return", -40.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == pytest.approx(Decimal("60.00"), abs=Decimal("0.01"))

    def test_floored_at_zero(self, session):
        """When personal refunds exceed personal spend, the total floors at 0."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True)
        _make_invoice(session, project_id, "materials_services", 40.0, payment_method_id=pm_id)
        _make_invoice(session, project_id, "return", -100.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == Decimal("0")

    def test_soft_deleted_personal_method_still_counts(self, session):
        """Soft-deleted (is_active=false) personal-payment method still contributes."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_personal_payment=True, is_active=False)
        _make_invoice(session, project_id, "materials_services", 250.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == pytest.approx(Decimal("250.00"), abs=Decimal("0.01"))

    def test_empty_project_returns_zero(self, session):
        """Project with no invoices returns 0."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project_id)

        assert total == Decimal("0")

    def test_project_without_company_returns_zero(self, session):
        """Project with no company has no personal-flagged methods — returns 0."""
        user_id = _make_user(session)
        project = ProjectModel(id=uuid4(), name="No Co Project", owner_id=user_id)
        session.add(project)
        session.flush()
        _make_invoice(session, project.id, "materials_services", 100.0)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_personal_spent(project.id)

        assert total == Decimal("0")
