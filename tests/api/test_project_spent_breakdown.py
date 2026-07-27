"""Spend breakdown tests: which spend is credit-funded vs out-of-pocket personal.

``SqlAlchemyProjectSpentReader`` reports ``total`` and ``by_credits`` per project. The
credit share must follow exactly the same rule as the Expense page's "spent by company"
KPI — both go through ``is_company_paid`` — so the projects card and the Expense page can
never disagree. Personal spend is whatever is left over, so labor never lands in credits.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.labor_entry import LaborEntryModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.worker import WorkerModel


def _reader(session):
    from app.infrastructure.database.repositories.sqlalchemy_project_spent_reader import (
        SqlAlchemyProjectSpentReader,
    )

    return SqlAlchemyProjectSpentReader(session)


@pytest.fixture
def credit_project(invitation_app):
    """Project owned by a company with one company-payment method and one regular method."""
    from app import db

    with invitation_app.app_context():
        owner_id = UUID(invitation_app._test_admin_user_id)
        now = datetime.now(timezone.utc)

        company = CompanyModel(
            id=uuid4(),
            legal_name="Credit Split SARL",
            address="1 rue du Credit",
            created_by=owner_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(company)
        db.session.flush()

        company_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Credit Split SARL",
            is_builtin=True,
            is_active=True,
            is_company_payment=True,
            created_by=owner_id,
            created_at=now,
            updated_at=now,
        )
        regular_pm = PaymentMethodModel(
            id=uuid4(),
            company_id=company.id,
            label="Cash",
            is_builtin=True,
            is_active=True,
            is_company_payment=False,
            created_by=owner_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add_all([company_pm, regular_pm])
        db.session.flush()

        project = ProjectModel(name="Credit Split Project", owner_id=owner_id, company_id=company.id)
        db.session.add(project)
        db.session.commit()

        yield {
            "project_id": project.id,
            "company_id": company.id,
            "company_pm_id": company_pm.id,
            "regular_pm_id": regular_pm.id,
            "owner_id": owner_id,
        }

        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM invoices WHERE project_id = :id"),
            {"id": str(project.id)},
        )
        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM projects WHERE id = :id"),
            {"id": str(project.id)},
        )
        db.session.commit()


def _add_invoice(session, project_id, *, number, amount, **kwargs):
    """Add a one-line invoice worth `amount` HT (no VAT unless caller overrides items)."""
    invoice = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=number,
        type=kwargs.pop("type", "materials_services"),
        issue_date=kwargs.pop("issue_date", date(2025, 5, 1)),
        recipient_name="Supplier",
        items=kwargs.pop("items", [{"quantity": "1", "unit_price": str(amount)}]),
        **kwargs,
    )
    session.add(invoice)
    session.commit()
    return invoice


def test_company_method_counts_as_credit(invitation_app, credit_project):
    """An invoice paid with a company-flagged method is credit-funded."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_invoice(db.session, pid, number="CS-001", amount=300, payment_method_id=credit_project["company_pm_id"])

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == pytest.approx(Decimal("300"))
        assert result.by_credits == pytest.approx(Decimal("300"))


def test_regular_method_is_personal(invitation_app, credit_project):
    """An invoice paid with a non-company method counts only in the total."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_invoice(db.session, pid, number="CS-002", amount=250, payment_method_id=credit_project["regular_pm_id"])

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == pytest.approx(Decimal("250"))
        assert result.by_credits == Decimal("0")


def test_null_payment_method_is_personal(invitation_app, credit_project):
    """An invoice with no payment method recorded is not credit-funded."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_invoice(db.session, pid, number="CS-003", amount=125)

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == pytest.approx(Decimal("125"))
        assert result.by_credits == Decimal("0")


@pytest.mark.parametrize(
    "refunded_by,expected_credits",
    [
        ("company", Decimal("400")),  # company reimbursed it → company money
        (None, Decimal("400")),  # legacy row, no refunded_by → counts as company
        ("both", Decimal("400")),  # company did reimburse, split unknown
        ("bank", Decimal("0")),  # bank's money, never the company's
    ],
)
def test_refunded_rows_follow_refunded_by(invitation_app, credit_project, refunded_by, expected_credits):
    """A refunded expense counts as credit spend unless the bank issued the refund."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_invoice(
            db.session,
            pid,
            number=f"CS-REF-{refunded_by}",
            amount=400,
            payment_method_id=credit_project["regular_pm_id"],
            refundable_status="refunded",
            refunded_by=refunded_by,
        )

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == pytest.approx(Decimal("400"))
        assert result.by_credits == pytest.approx(expected_credits)


def test_released_funds_excluded_from_both_figures(invitation_app, credit_project):
    """Released funds are budget inflow — they never count as spend of either kind."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_invoice(
            db.session,
            pid,
            number="CS-RF",
            amount=90000,
            type="released_funds",
            payment_method_id=credit_project["company_pm_id"],
        )

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == Decimal("0")
        assert result.by_credits == Decimal("0")


def test_company_refund_nets_credits_down(invitation_app, credit_project):
    """A company-issued refund carries negative lines and reduces credit spend."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        pm = credit_project["company_pm_id"]
        _add_invoice(db.session, pid, number="CS-004", amount=500, payment_method_id=pm)
        _add_invoice(db.session, pid, number="CS-005", amount=-200, type="refund", payment_method_id=pm)

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == pytest.approx(Decimal("300"))
        assert result.by_credits == pytest.approx(Decimal("300"))


def test_credits_floored_at_zero(invitation_app, credit_project):
    """Refunds exceeding company spend floor credits at 0 rather than going negative."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        pm = credit_project["company_pm_id"]
        _add_invoice(db.session, pid, number="CS-006", amount=100, payment_method_id=pm)
        _add_invoice(db.session, pid, number="CS-007", amount=-350, type="refund", payment_method_id=pm)

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == pytest.approx(Decimal("-250"))
        assert result.by_credits == Decimal("0")


def test_soft_deleted_company_method_still_counts(invitation_app, credit_project):
    """Deactivating a payment method must not erase spend that already happened."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        pm_id = credit_project["company_pm_id"]
        _add_invoice(db.session, pid, number="CS-008", amount=175, payment_method_id=pm_id)

        db.session.query(PaymentMethodModel).filter_by(id=pm_id).update({"is_active": False})
        db.session.commit()

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.by_credits == pytest.approx(Decimal("175"))


def test_labor_never_counts_as_credit(invitation_app, credit_project):
    """Labor entries are always out-of-pocket: they raise total, never credits."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]

        worker = WorkerModel(
            id=uuid4(),
            project_id=pid,
            name="Credit Split Worker",
            daily_rate=Decimal("200.00"),
            is_active=True,
        )
        db.session.add(worker)
        db.session.flush()
        db.session.add(LaborEntryModel(id=uuid4(), worker_id=worker.id, date=date(2025, 5, 2), shift_type="full"))
        db.session.commit()

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.total == pytest.approx(Decimal("200"))
        assert result.by_credits == Decimal("0")


def test_company_methods_do_not_leak_across_companies(invitation_app, credit_project):
    """A second company's project must not inherit the first company's payment methods."""
    from app import db

    with invitation_app.app_context():
        owner_id = credit_project["owner_id"]
        now = datetime.now(timezone.utc)

        other_company = CompanyModel(
            id=uuid4(),
            legal_name="Other SARL",
            address="2 rue Ailleurs",
            created_by=owner_id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(other_company)
        db.session.flush()
        other_project = ProjectModel(name="Other Company Project", owner_id=owner_id, company_id=other_company.id)
        db.session.add(other_project)
        db.session.commit()

        pid1 = credit_project["project_id"]
        pid2 = other_project.id

        _add_invoice(db.session, pid1, number="CS-009", amount=600, payment_method_id=credit_project["company_pm_id"])
        # Same method id, but this project belongs to a different company.
        _add_invoice(db.session, pid2, number="CS-010", amount=700, payment_method_id=credit_project["company_pm_id"])

        result = _reader(db.session).sum_spent_by_projects([pid1, pid2])
        assert result[pid1].by_credits == pytest.approx(Decimal("600"))
        assert result[pid2].total == pytest.approx(Decimal("700"))
        assert result[pid2].by_credits == Decimal("0")

        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM invoices WHERE project_id = :id"),
            {"id": str(pid2)},
        )
        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM projects WHERE id = :id"),
            {"id": str(pid2)},
        )
        db.session.commit()


def test_project_without_company_has_no_credit_spend(invitation_app):
    """A project with no company_id resolves to zero credit spend without crashing."""
    from app import db

    with invitation_app.app_context():
        owner_id = UUID(invitation_app._test_admin_user_id)
        project = ProjectModel(name="No Company Project", owner_id=owner_id)
        db.session.add(project)
        db.session.commit()

        _add_invoice(db.session, project.id, number="CS-011", amount=80)

        result = _reader(db.session).sum_spent_by_projects([project.id])[project.id]
        assert result.total == pytest.approx(Decimal("80"))
        assert result.by_credits == Decimal("0")

        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM invoices WHERE project_id = :id"),
            {"id": str(project.id)},
        )
        db.session.execute(
            __import__("sqlalchemy").text("DELETE FROM projects WHERE id = :id"),
            {"id": str(project.id)},
        )
        db.session.commit()


def test_credit_figure_matches_expense_page_kpi(invitation_app, credit_project):
    """The card's credit figure must equal the Expense page's sum_company_spent, always.

    This is the whole point of routing both through ``is_company_paid`` — if these two ever
    diverge, the same concept shows two numbers in the product.
    """
    from app import db
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_invoice(
            db.session,
            pid,
            number="CS-012",
            amount=1000,
            payment_method_id=credit_project["company_pm_id"],
            items=[{"quantity": "2", "unit_price": "500", "vat_rate": "20"}],
        )
        _add_invoice(db.session, pid, number="CS-013", amount=333, payment_method_id=credit_project["regular_pm_id"])

        from_card = _reader(db.session).sum_spent_by_projects([pid])[pid].by_credits
        from_expense_page = SQLAlchemyInvoiceRepository(db.session).sum_company_spent(pid)

        assert from_card == pytest.approx(from_expense_page)
        # 2 x 500 x 1.20 = 1200 TTC
        assert from_card == pytest.approx(Decimal("1200"))


def test_empty_project_list_returns_empty_dict(invitation_app):
    """No ids in, no query out."""
    from app import db

    with invitation_app.app_context():
        assert _reader(db.session).sum_spent_by_projects([]) == {}
