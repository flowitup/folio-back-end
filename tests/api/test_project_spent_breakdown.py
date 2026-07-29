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
        _add_invoice(db.session, pid, number="CS-005", amount=-200, type="return", payment_method_id=pm)

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
        _add_invoice(db.session, pid, number="CS-007", amount=-350, type="return", payment_method_id=pm)

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


# ---------------------------------------------------------------------------
# Labor accrual vs settlement: paid, unpaid, and the reconciliation invariant
# ---------------------------------------------------------------------------


def _add_labor_entries(session, project_id, *, days, daily_rate="200.00", name="Worker"):
    """Log `days` full shifts for a fresh worker; accrues days x daily_rate."""
    worker = WorkerModel(
        id=uuid4(),
        project_id=project_id,
        name=name,
        daily_rate=Decimal(daily_rate),
        is_active=True,
    )
    session.add(worker)
    session.flush()
    for i in range(days):
        session.add(LaborEntryModel(id=uuid4(), worker_id=worker.id, date=date(2025, 6, i + 1), shift_type="full"))
    session.commit()
    return worker


def test_labor_invoice_settles_accrual_without_inflating_total(invitation_app, credit_project):
    """Paying a worker records who funded the work; it must not bill the work twice."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_labor_entries(db.session, pid, days=3)  # accrues 600

        before = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert before.total == pytest.approx(Decimal("600"))
        assert before.labor_unpaid == pytest.approx(Decimal("600"))

        _add_invoice(
            db.session,
            pid,
            number="CS-LAB-1",
            amount=250,
            type="labor",
            payment_method_id=credit_project["regular_pm_id"],
        )

        after = _reader(db.session).sum_spent_by_projects([pid])[pid]
        # Total is unchanged: the payment settles part of the accrual, it is not new cost.
        assert after.total == pytest.approx(Decimal("600"))
        assert after.labor_paid == pytest.approx(Decimal("250"))
        assert after.labor_unpaid == pytest.approx(Decimal("350"))
        assert after.personal == pytest.approx(Decimal("250"))


def test_unpaid_is_everything_when_no_labor_invoice_exists(invitation_app, credit_project):
    """Attendance with no payment yet is entirely owed."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_labor_entries(db.session, pid, days=2)

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.labor_accrued == pytest.approx(Decimal("400"))
        assert result.labor_paid == Decimal("0")
        assert result.labor_unpaid == pytest.approx(Decimal("400"))
        assert result.personal == Decimal("0")


def test_unpaid_floors_at_zero_when_workers_are_overpaid(invitation_app, credit_project):
    """Paying more than was accrued yields 0 owed, never a negative."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_labor_entries(db.session, pid, days=1)  # accrues 200
        _add_invoice(
            db.session,
            pid,
            number="CS-LAB-2",
            amount=500,
            type="labor",
            payment_method_id=credit_project["regular_pm_id"],
        )

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.labor_unpaid == Decimal("0")


def test_company_paid_labor_invoice_is_credit_not_personal(invitation_app, credit_project):
    """Who settled the wage decides which bucket it lands in."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_labor_entries(db.session, pid, days=2)  # 400
        _add_invoice(
            db.session,
            pid,
            number="CS-LAB-3",
            amount=150,
            type="labor",
            payment_method_id=credit_project["company_pm_id"],
        )

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.by_credits == pytest.approx(Decimal("150"))
        assert result.personal == Decimal("0")
        assert result.labor_unpaid == pytest.approx(Decimal("250"))


def test_personal_by_type_sums_to_personal(invitation_app, credit_project):
    """Every personal euro is attributed to exactly one expense type."""
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        cash = credit_project["regular_pm_id"]
        _add_labor_entries(db.session, pid, days=1)
        _add_invoice(db.session, pid, number="CS-T1", amount=100, type="labor", payment_method_id=cash)
        _add_invoice(db.session, pid, number="CS-T2", amount=300, type="materials_services", payment_method_id=cash)
        _add_invoice(db.session, pid, number="CS-T3", amount=50, type="others", payment_method_id=cash)
        _add_invoice(db.session, pid, number="CS-T4", amount=-20, type="return", payment_method_id=cash)

        result = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert result.personal_by_type["labor"] == pytest.approx(Decimal("100"))
        assert result.personal_by_type["materials_services"] == pytest.approx(Decimal("300"))
        assert result.personal_by_type["others"] == pytest.approx(Decimal("50"))
        assert result.personal_by_type["return"] == pytest.approx(Decimal("-20"))
        assert sum(result.personal_by_type.values()) == pytest.approx(result.personal)


def test_reconciliation_invariant_holds_on_mixed_data(invitation_app, credit_project):
    """credits + personal + unpaid == total, on data exercising every branch.

    This is the guard for the whole model: if any figure is double counted or dropped,
    the three buckets stop adding up to the project's spend.
    """
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        company = credit_project["company_pm_id"]
        cash = credit_project["regular_pm_id"]

        _add_labor_entries(db.session, pid, days=4)  # 800 accrued
        _add_invoice(db.session, pid, number="CS-M1", amount=1000, type="labor", payment_method_id=company)
        _add_invoice(db.session, pid, number="CS-M2", amount=90, type="labor", payment_method_id=cash)
        _add_invoice(db.session, pid, number="CS-M3", amount=400, type="materials_services", payment_method_id=company)
        _add_invoice(db.session, pid, number="CS-M4", amount=250, type="materials_services", payment_method_id=cash)
        _add_invoice(db.session, pid, number="CS-M5", amount=75, type="others", payment_method_id=cash)
        _add_invoice(db.session, pid, number="CS-M6", amount=99999, type="released_funds")

        r = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert r.by_credits + r.personal + r.labor_unpaid == pytest.approx(r.total)

        # Labor: 800 accrued but 1090 actually paid out (1000 company + 90 cash), so labor
        # costs the larger figure — the 290 over the accrual left an account for real.
        # Plus non-labor invoices 400 + 250 + 75. released_funds stays out of every figure.
        assert r.labor_accrued == pytest.approx(Decimal("800"))
        assert r.labor_paid == pytest.approx(Decimal("1090"))
        assert r.labor_unpaid == Decimal("0")
        assert r.total == pytest.approx(Decimal("1090") + Decimal("400") + Decimal("250") + Decimal("75"))


def test_wages_paid_without_logged_attendance_still_count_as_spend(invitation_app, credit_project):
    """Paying a worker whose days were never logged is real spend, not a rounding hole.

    Guards the max(accrued, paid) rule: without it the payment would vanish from `total`
    and the reconciliation invariant would break whenever workers are overpaid.
    """
    from app import db

    with invitation_app.app_context():
        pid = credit_project["project_id"]
        _add_invoice(
            db.session,
            pid,
            number="CS-LAB-NOLOG",
            amount=700,
            type="labor",
            payment_method_id=credit_project["regular_pm_id"],
        )

        r = _reader(db.session).sum_spent_by_projects([pid])[pid]
        assert r.labor_accrued == Decimal("0")
        assert r.labor_paid == pytest.approx(Decimal("700"))
        assert r.labor_unpaid == Decimal("0")
        assert r.total == pytest.approx(Decimal("700"))
        assert r.by_credits + r.personal + r.labor_unpaid == pytest.approx(r.total)
