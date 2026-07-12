"""Unit tests for SQLAlchemyInvoiceRepository.sum_company_spent.

Uses the shared function-scoped SQLite session from conftest.  All IDs
are UUID objects to satisfy SQLite's UUID(as_uuid=True) columns.
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


def _make_payment_method(session, company_id: UUID, is_company_payment: bool, is_active: bool = True) -> UUID:
    now = _now()
    pm = PaymentMethodModel(
        id=uuid4(),
        company_id=company_id,
        label=f"PM-{uuid4().hex[:6]}",
        is_builtin=False,
        is_active=is_active,
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
    refundable_status=None,
    refunded_by=None,
    payment_method_id: UUID | None = None,
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSumCompanySpent:
    def test_refunded_ms_invoice_counts(self, session):
        """M&S invoice with refundable_status='refunded' counts."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        _make_invoice(session, project_id, "materials_services", 200.0, refundable_status="refunded")

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("200.00"), abs=Decimal("0.01"))

    def test_bank_refunded_ms_invoice_not_counted(self, session):
        """Bank-refunded expense is the bank's money, not company spend."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        _make_invoice(
            session,
            project_id,
            "materials_services",
            200.0,
            refundable_status="refunded",
            refunded_by="bank",
        )

        repo = SQLAlchemyInvoiceRepository(session)

        assert repo.sum_company_spent(project_id) == Decimal("0")

    def test_both_refunded_ms_invoice_counts_as_company_spend(self, session):
        """'both' keeps counting: the company did reimburse (split unknown)."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        _make_invoice(
            session,
            project_id,
            "materials_services",
            200.0,
            refundable_status="refunded",
            refunded_by="both",
        )

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("200.00"), abs=Decimal("0.01"))

    def test_explicit_company_refunded_by_counts(self, session):
        """refunded_by='company' counts, same as legacy NULL."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        _make_invoice(
            session,
            project_id,
            "materials_services",
            200.0,
            refundable_status="refunded",
            refunded_by="company",
        )

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("200.00"), abs=Decimal("0.01"))

    def test_company_paid_labor_counts(self, session):
        """Labor invoice paid with is_company_payment method counts."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(session, project_id, "labor", 500.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("500.00"), abs=Decimal("0.01"))

    def test_company_paid_ms_counts(self, session):
        """M&S invoice paid with is_company_payment method counts even without refunded status."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(session, project_id, "materials_services", 150.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("150.00"), abs=Decimal("0.01"))

    def test_company_paid_others_counts(self, session):
        """'others' type invoice paid with is_company_payment method counts."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(session, project_id, "others", 75.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("75.00"), abs=Decimal("0.01"))

    def test_both_conditions_on_one_invoice_counts_once(self, session):
        """Invoice satisfying both refunded AND company-payment is not double-counted."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(
            session,
            project_id,
            "materials_services",
            300.0,
            refundable_status="refunded",
            payment_method_id=pm_id,
        )

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        # Must be exactly 300, not 600
        assert total == pytest.approx(Decimal("300.00"), abs=Decimal("0.01"))

    def test_non_flagged_method_not_counted(self, session):
        """Invoice paid with a non-company-payment method and no refunded status is excluded."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=False)
        _make_invoice(session, project_id, "materials_services", 999.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == Decimal("0")

    def test_released_funds_never_counted(self, session):
        """released_funds invoices are excluded even when payment method is company-flagged."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(
            session,
            project_id,
            "released_funds",
            10000.0,
            refundable_status=None,
            payment_method_id=pm_id,
        )

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == Decimal("0")

    def test_soft_deleted_company_method_still_counts(self, session):
        """Soft-deleted (is_active=false) company-payment method still contributes to total."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True, is_active=False)
        _make_invoice(session, project_id, "materials_services", 250.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("250.00"), abs=Decimal("0.01"))

    def test_empty_project_returns_zero(self, session):
        """Project with no invoices returns 0."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == Decimal("0")

    def test_only_refundable_status_not_refunded_excluded(self, session):
        """Invoice with refundable_status='refundable' (not 'refunded') is excluded."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        _make_invoice(session, project_id, "materials_services", 100.0, refundable_status="refundable")

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == Decimal("0")

    def test_company_issued_refund_reduces_total(self, session):
        """A refund paid via a company method (negative lines) nets the total down."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        # Company-paid expense of 100, then the company issues a 40 refund on the
        # same company method (stored as a negative line).
        _make_invoice(session, project_id, "materials_services", 100.0, payment_method_id=pm_id)
        _make_invoice(session, project_id, "refund", -40.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("60.00"), abs=Decimal("0.01"))

    def test_supplier_refund_without_company_method_not_counted(self, session):
        """A refund NOT paid via a company method is ignored — total unchanged."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        company_pm = _make_payment_method(session, company_id, is_company_payment=True)
        other_pm = _make_payment_method(session, company_id, is_company_payment=False)
        _make_invoice(session, project_id, "materials_services", 100.0, payment_method_id=company_pm)
        # Supplier refund on a non-company method must not touch company_spent.
        _make_invoice(session, project_id, "refund", -40.0, payment_method_id=other_pm)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == pytest.approx(Decimal("100.00"), abs=Decimal("0.01"))

    def test_company_spent_floored_at_zero(self, session):
        """When company refunds exceed company spend, the total floors at 0, not negative."""
        user_id = _make_user(session)
        company_id = _make_company(session, user_id)
        project_id = _make_project(session, user_id, company_id)
        pm_id = _make_payment_method(session, company_id, is_company_payment=True)
        _make_invoice(session, project_id, "materials_services", 40.0, payment_method_id=pm_id)
        _make_invoice(session, project_id, "refund", -100.0, payment_method_id=pm_id)

        repo = SQLAlchemyInvoiceRepository(session)
        total = repo.sum_company_spent(project_id)

        assert total == Decimal("0")
