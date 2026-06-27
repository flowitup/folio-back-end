"""SQLAlchemy adapter implementing ProjectSpentReaderPort.

Aggregates project spend as:
    spent(project) = labor_cost + Σ(non-released_funds invoice line totals)

Labor cost mirrors the effective_cost expression in SqlAlchemyProjectTagRepository:
  - shift_type IS NULL  → 0 (supplement-only rows contribute no cost)
  - shift_type = 'half' → daily_rate * 0.5
  - shift_type = 'overtime' → daily_rate * 1.5
  - else               → daily_rate * 1.0
  - amount_override coalesced over the computed value

Invoice cost is summed in Python from the items JSON (dialect-safe, same as
sum_expense_by_tag), filtering out released_funds invoices.

Refund invoices carry negative line items and net down the total automatically.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import case as sa_case, func
from sqlalchemy.orm import Session

from app.application.projects.ports import ProjectSpentReaderPort
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.labor_entry import LaborEntryModel
from app.infrastructure.database.models.worker import WorkerModel


class SqlAlchemyProjectSpentReader(ProjectSpentReaderPort):
    """Batch-reads labor + invoice totals for a list of project UUIDs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def sum_spent_by_projects(self, project_ids: list[UUID]) -> dict[UUID, Decimal]:
        """Return {project_id: Decimal} for each id; missing projects map to 0."""
        if not project_ids:
            return {}

        result: dict[UUID, Decimal] = {pid: Decimal("0") for pid in project_ids}

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
            cost = Decimal(str(row.labor_cost)) if row.labor_cost is not None else Decimal("0")
            result[pid] = result.get(pid, Decimal("0")) + cost

        # ------------------------------------------------------------------
        # Query 2: invoice line totals grouped by project_id
        # Excludes released_funds (budget inflow, not construction expense).
        # Items JSON is loaded in Python to avoid dialect-specific JSONB arithmetic.
        # ------------------------------------------------------------------
        invoice_rows = (
            self._session.query(
                InvoiceModel.project_id,
                InvoiceModel.items,
            )
            .filter(
                InvoiceModel.project_id.in_(project_ids),
                InvoiceModel.type != "released_funds",
            )
            .all()
        )

        for row in invoice_rows:
            pid = row.project_id
            items = row.items or []
            total = Decimal("0")
            for item in items:
                qty = Decimal(str(item.get("quantity", 0)))
                price = Decimal(str(item.get("unit_price", 0)))
                total += qty * price
            result[pid] = result.get(pid, Decimal("0")) + total

        return result
