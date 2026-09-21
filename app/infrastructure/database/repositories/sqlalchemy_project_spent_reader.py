"""SQLAlchemy adapter implementing ProjectSpentReaderPort.

Aggregates project spend, split by funding source:

    labor_unpaid(project) = Σ over workers of max(0, accrued_w − paid_w)
    total(project)        = labor_paid + labor_unpaid
                            + Σ(invoice totals except released_funds and labor)
    invoiced(project)     = Σ(invoice totals except released_funds)
    by_credits(project)   = Σ(invoice totals funded with company money, except released_funds)
    personal(project)     = Σ(invoice totals funded out of pocket, except released_funds)

Labor is accrued from attendance entries and settled by labor-type invoices. Those invoices
say *who paid* rather than adding cost, so they are excluded from ``total`` — counting the
accrual and the payment would bill the same work twice. They still classify into
by_credits/personal, because that money really did leave someone's account.

What is owed is owed **per worker**: paying one worker twice over never settles another
worker's days, so each worker's shortfall is floored at zero on its own and the shortfalls
are summed. A labor invoice with no ``worker_id`` settles nobody's accrual — there is no
worker to credit it to — so it raises ``labor_paid`` and ``total`` without reducing what
anyone is owed. ``labor_unpaid`` therefore no longer equals ``labor_accrued − labor_paid``;
that difference is exactly the money paid out beyond, or beside, what was logged.

Invariant: ``by_credits + personal + labor_unpaid == total``.

``invoiced`` is the same ledger the invoice list adds up client-side: every spend invoice
plus every credit note, and nothing accrued. It sits alongside ``total`` rather than
replacing it, because the two answer different questions — what the project has been
billed, versus what the work has cost including wages nobody has paid yet.

Labor cost uses the effective_cost expression shared with the labor summary endpoints:
  - shift_type IS NULL  → 0 (supplement-only rows contribute no cost)
  - shift_type = 'half' → daily_rate * 0.5
  - shift_type = 'overtime' → daily_rate * 1.5
  - else               → daily_rate * 1.0
  - amount_override coalesced over the computed value

``daily_rate`` there is the rate in force on the entry's own date — the worker's latest
rate change effective on or before that date, falling back to the worker's base rate.
A back-dated rate change therefore moves this total exactly as it moves attendance and
salary figures, instead of leaving spend on the base rate.

Invoice totals go through the shared ``items_total`` (TTC) and ``is_company_paid`` rules so
this breakdown always agrees with the Expense-page KPIs — same helpers, same numbers.
Computed in Python because JSONB item arithmetic varies by dialect.

Refund invoices carry negative line items and net down both totals automatically.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import case as sa_case, func, select
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
from app.infrastructure.database.models.worker_rate_change import WorkerRateChangeModel


class SqlAlchemyProjectSpentReader(ProjectSpentReaderPort):
    """Batch-reads labor + invoice totals for a list of project UUIDs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def sum_spent_by_projects(self, project_ids: list[UUID]) -> dict[UUID, ProjectSpent]:
        """Return {project_id: ProjectSpent} for each id; missing projects map to zero."""
        if not project_ids:
            return {}

        zero = Decimal("0")
        # Labor is reconciled per worker, so both sides are kept keyed by worker id.
        # Labor invoices with no worker attached settle nobody and live in their own bucket.
        accrued_by_worker: dict[UUID, dict[UUID, Decimal]] = {pid: {} for pid in project_ids}
        paid_by_worker: dict[UUID, dict[UUID, Decimal]] = {pid: {} for pid in project_ids}
        labor_paid_unattributed: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        non_labor_invoices: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        credits: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        personal: dict[UUID, Decimal] = {pid: zero for pid in project_ids}
        personal_by_type: dict[UUID, dict[str, Decimal]] = {pid: {} for pid in project_ids}

        # ------------------------------------------------------------------
        # Query 1: labor cost grouped by project_id
        # labor_entries has no project_id column; must join via workers.
        # Same effective_cost case expression as the labor summary rollups.
        # ------------------------------------------------------------------
        shift_multiplier = sa_case(
            (LaborEntryModel.shift_type == "half", Decimal("0.5")),
            (LaborEntryModel.shift_type == "overtime", Decimal("1.5")),
            else_=Decimal("1.0"),
        )
        # The rate in force on the entry's own date: the rate-change row with the greatest
        # effective_date <= that date, else the worker's base rate. Correlated scalar
        # subqueries are excluded from GROUP BY, so grouping stays on project_id.
        # Mirrors get_summary in sqlalchemy_labor_entry.py — the two must agree or a
        # back-dated raise moves salaries without moving project spend.
        eff_rate = func.coalesce(
            select(WorkerRateChangeModel.daily_rate)
            .where(WorkerRateChangeModel.worker_id == LaborEntryModel.worker_id)
            .where(WorkerRateChangeModel.effective_date <= LaborEntryModel.date)
            .order_by(WorkerRateChangeModel.effective_date.desc())
            .limit(1)
            .correlate(LaborEntryModel, WorkerModel)
            .scalar_subquery(),
            WorkerModel.daily_rate,
        )
        shift_cost = func.coalesce(
            LaborEntryModel.amount_override,
            eff_rate * shift_multiplier,
        )
        effective_cost = sa_case(
            (LaborEntryModel.shift_type.is_(None), 0),
            else_=shift_cost,
        )

        labor_rows = (
            self._session.query(
                WorkerModel.project_id.label("project_id"),
                WorkerModel.id.label("worker_id"),
                func.sum(effective_cost).label("labor_cost"),
            )
            .join(LaborEntryModel, LaborEntryModel.worker_id == WorkerModel.id)
            .filter(WorkerModel.project_id.in_(project_ids), LaborEntryModel.status == "validated")
            .group_by(WorkerModel.project_id, WorkerModel.id)
            .all()
        )

        for row in labor_rows:
            pid = row.project_id
            cost = Decimal(str(row.labor_cost)) if row.labor_cost is not None else zero
            by_worker = accrued_by_worker.setdefault(pid, {})
            by_worker[row.worker_id] = by_worker.get(row.worker_id, zero) + cost

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
                InvoiceModel.worker_id,
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
                # Only a worker-tagged payment settles that worker's days; an untagged one
                # is money out with nobody's accrual to cancel.
                if row.worker_id is None:
                    labor_paid_unattributed[pid] = labor_paid_unattributed.get(pid, zero) + amount
                else:
                    by_worker = paid_by_worker.setdefault(pid, {})
                    by_worker[row.worker_id] = by_worker.get(row.worker_id, zero) + amount
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
            accrued_w = accrued_by_worker.get(pid, {})
            paid_w = paid_by_worker.get(pid, {})
            accrued = sum(accrued_w.values(), zero)
            paid = sum(paid_w.values(), zero) + labor_paid_unattributed.get(pid, zero)
            # Per worker, and only then summed: overpaying one worker must not cancel what
            # another is still owed, and no worker's shortfall may go negative.
            unpaid = sum(
                (max(cost - paid_w.get(worker_id, zero), zero) for worker_id, cost in accrued_w.items()),
                zero,
            )
            # Labor costs what was paid out plus what is still owed. Where a worker was
            # paid more than they logged — wages paid without the attendance recorded, or a
            # payment tagged to nobody — the extra really did leave someone's account, so it
            # counts as spend rather than vanishing. This is also what keeps
            # `by_credits + personal + labor_unpaid == total` true: by_credits + personal
            # already contains every labor invoice.
            result[pid] = ProjectSpent(
                total=paid + unpaid + non_labor_invoices.get(pid, zero),
                invoiced=paid + non_labor_invoices.get(pid, zero),
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
