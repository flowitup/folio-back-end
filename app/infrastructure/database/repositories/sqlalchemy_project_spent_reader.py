"""SQLAlchemy adapter implementing ProjectSpentReaderPort.

Aggregates project spend, split by funding source:

    total(project)      = max(labor_accrued, labor_paid)
                          + Σ(invoice totals except released_funds and labor)
    by_credits(project) = Σ(invoice totals funded with company money, except released_funds)
    personal(project)   = Σ(invoice totals funded out of pocket, except released_funds)
    labor_unpaid        = labor_accrued − labor_paid

Labor is accrued from attendance entries and settled by labor-type invoices. Those invoices
say *who paid* rather than adding cost, so they are excluded from ``total`` — counting the
accrual and the payment would bill the same work twice. They still classify into
by_credits/personal, because that money really did leave someone's account.

Invariant: ``by_credits + personal + labor_unpaid == total``.

Labor cost mirrors the effective_cost expression in SqlAlchemyProjectTagRepository:
  - shift_type IS NULL  → 0 (supplement-only rows contribute no cost)
  - shift_type = 'half' → daily_rate * 0.5
  - shift_type = 'overtime' → daily_rate * 1.5
  - else               → daily_rate * 1.0
  - amount_override coalesced over the computed value

Invoice totals go through the shared ``items_total`` (TTC) and ``is_company_paid`` rules so
this breakdown always agrees with the Expense-page KPIs — same helpers, same numbers.
Computed in Python because JSONB item arithmetic varies by dialect.

Refund invoices carry negative line items and net down both totals automatically.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import case as sa_case, func
from sqlalchemy.orm import Session

from app.application.projects.ports import ProjectSpent, ProjectSpentReaderPort
from app.infrastructure.database.invoice_spend_rules import (
    is_company_paid,
    items_total,
    load_company_paid_method_ids,
)
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.labor_entry import LaborEntryModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.worker import WorkerModel


class SqlAlchemyProjectSpentReader(ProjectSpentReaderPort):
    """Batch-reads labor + invoice totals for a list of project UUIDs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def sum_spent_by_projects(self, project_ids: list[UUID]) -> dict[UUID, ProjectSpent]:
        """Return {project_id: ProjectSpent} for each id; missing projects map to zero."""
        if not project_ids:
            return {}

        zero = Decimal("0")
        # labor_accrued doubles as the seed of `total`; invoice totals are added on top.
        labor_accrued: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        labor_paid: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        non_labor_invoices: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        credits: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        personal: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        personal_by_type: dict[UUID, dict[str, Decimal]] = {pid: {} for pid in project_ids}

        # ------------------------------------------------------------------
        # Query 1: labor cost grouped by project_id
        # labor_entries has no project_id column; must join via workers.
        # Mirrors the effective_cost case expression in sum_labor_cost_by_tag.
        # ------------------------------------------------------------------
        shift_multiplier = sa_case(
            (LaborEntryModel.shift_type == "half", Decimal("0.5")),
            (LaborEntryModel.shift_type == "overtime", Decimal("1.5")),
            else_=Decimal("1.0"),
        )
        shift_cost = func.coalesce(
            LaborEntryModel.amount_override,
            WorkerModel.daily_rate * shift_multiplier,
        )
        effective_cost = sa_case(
            (LaborEntryModel.shift_type.is_(None), 0),
            else_=shift_cost,
        )

        labor_rows = (
            self._session.query(
                WorkerModel.project_id.label("project_id"),
                func.sum(effective_cost).label("labor_cost"),
            )
            .join(LaborEntryModel, LaborEntryModel.worker_id == WorkerModel.id)
            .filter(WorkerModel.project_id.in_(project_ids))
            .group_by(WorkerModel.project_id)
            .all()
        )

        for row in labor_rows:
            pid = row.project_id
            cost = Decimal(str(row.labor_cost)) if row.labor_cost is not None else zero
            labor_accrued[pid] = labor_accrued.get(pid, zero) + cost

        # ------------------------------------------------------------------
        # Query 2 + 3: resolve each project's company, then that company's
        # company-payment method ids. Projects in one list may span companies,
        # so the lookup is keyed per company — a method never leaks across them.
        # ------------------------------------------------------------------
        company_by_project: dict[UUID, UUID | None] = {
            row.id: row.company_id
            for row in self._session.query(ProjectModel.id, ProjectModel.company_id)
            .filter(ProjectModel.id.in_(project_ids))
            .all()
        }
        methods_by_company = load_company_paid_method_ids(
            self._session, {cid for cid in company_by_project.values() if cid is not None}
        )

        # ------------------------------------------------------------------
        # Query 4: invoice totals grouped by project_id, classified by funding source.
        # Excludes released_funds (budget inflow, not construction expense).
        # ------------------------------------------------------------------
        invoice_rows = (
            self._session.query(
                InvoiceModel.project_id,
                InvoiceModel.type,
                InvoiceModel.items,
                InvoiceModel.payment_method_id,
                InvoiceModel.refundable_status,
                InvoiceModel.refunded_by,
            )
            .filter(
                InvoiceModel.project_id.in_(project_ids),
                InvoiceModel.type != "released_funds",
            )
            .all()
        )

        for row in invoice_rows:
            pid = row.project_id
            amount = items_total(row.items)

            if row.type == "labor":
                # A settlement of accrued labor, not new cost: it decides who funded the
                # work. Adding it to `total` on top of the accrual would double-bill it.
                labor_paid[pid] = labor_paid.get(pid, zero) + amount
            else:
                non_labor_invoices[pid] = non_labor_invoices.get(pid, zero) + amount

            company_id = company_by_project.get(pid)
            company_paid_ids = methods_by_company.get(company_id, set()) if company_id else set()
            if is_company_paid(
                payment_method_id=row.payment_method_id,
                refundable_status=row.refundable_status,
                refunded_by=row.refunded_by,
                company_paid_ids=company_paid_ids,
            ):
                credits[pid] = credits.get(pid, zero) + amount
            else:
                personal[pid] = personal.get(pid, zero) + amount
                by_type = personal_by_type[pid]
                by_type[row.type] = by_type.get(row.type, zero) + amount

        result: dict[UUID, ProjectSpent] = {}
        for pid in project_ids:
            accrued = labor_accrued.get(pid, zero)
            paid = labor_paid.get(pid, zero)
            # Overpaying workers must not produce negative "owed"; floor it.
            unpaid = max(accrued - paid, zero)
            # Labor costs whichever is larger. Normally that is the accrual, with payments
            # settling it. But paying more than was logged means the extra really did leave
            # someone's account — most often wages paid without the attendance being
            # recorded — so it counts as spend rather than vanishing. Taking the max is also
            # what keeps `by_credits + personal + labor_unpaid == total` true in both
            # directions: paid + max(accrued - paid, 0) == max(accrued, paid).
            result[pid] = ProjectSpent(
                total=max(accrued, paid) + non_labor_invoices.get(pid, zero),
                # Company refunds can exceed company spend; a negative "spent by credit"
                # is meaningless for the KPI, so floor it exactly as sum_company_spent does.
                by_credits=max(credits.get(pid, zero), zero),
                personal=max(personal.get(pid, zero), zero),
                labor_accrued=accrued,
                labor_paid=paid,
                labor_unpaid=unpaid,
                personal_by_type={k: v for k, v in personal_by_type[pid].items()},
            )
        return result
