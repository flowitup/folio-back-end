"""SQLAlchemy adapter implementing ProjectTagRepositoryPort, LaborCostReaderPort,
and ExpenseTotalReaderPort for the tags bounded context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import case as sa_case, func
from sqlalchemy.orm import Session

from app.domain.entities.project_tag import ProjectTag
from app.infrastructure.database.models.labor_entry import LaborEntryModel
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.project_tag import ProjectTagModel
from app.infrastructure.database.models.worker import WorkerModel


class SqlAlchemyProjectTagRepository:
    """Implements ProjectTagRepositoryPort + LaborCostReaderPort + ExpenseTotalReaderPort."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # ProjectTagRepositoryPort
    # ------------------------------------------------------------------

    def add(self, tag: ProjectTag) -> None:
        """Insert a new tag."""
        orm = ProjectTagModel(
            id=tag.id,
            project_id=tag.project_id,
            name=tag.name,
            color=tag.color,
            created_at=tag.created_at,
            updated_at=tag.updated_at,
        )
        self._session.add(orm)
        self._session.flush()

    def get_by_id(self, tag_id: UUID) -> Optional[ProjectTag]:
        """Return tag by UUID, or None if not found."""
        orm = self._session.get(ProjectTagModel, tag_id)
        return self._to_entity(orm) if orm is not None else None

    def list_by_project(self, project_id: UUID) -> list[ProjectTag]:
        """Return all tags for a project ordered by name ASC."""
        rows = (
            self._session.query(ProjectTagModel)
            .filter(ProjectTagModel.project_id == project_id)
            .order_by(ProjectTagModel.name.asc())
            .all()
        )
        return [self._to_entity(r) for r in rows]

    def save(self, tag: ProjectTag) -> None:
        """Persist updated tag fields."""
        orm = self._session.get(ProjectTagModel, tag.id)
        if orm is None:
            raise ValueError(f"ProjectTag {tag.id} not found — cannot save.")
        orm.name = tag.name
        orm.color = tag.color
        orm.updated_at = tag.updated_at or datetime.now(timezone.utc)
        self._session.flush()

    def delete(self, tag_id: UUID) -> None:
        """Delete tag by UUID. FK ON DELETE SET NULL handles downstream rows."""
        orm = self._session.get(ProjectTagModel, tag_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    def exists_name_in_project(self, project_id: UUID, name: str, exclude_tag_id: Optional[UUID] = None) -> bool:
        """Return True if a tag with this (project_id, name) already exists."""
        stripped = name.strip()
        query = self._session.query(ProjectTagModel).filter(
            ProjectTagModel.project_id == project_id,
            ProjectTagModel.name == stripped,
        )
        if exclude_tag_id is not None:
            query = query.filter(ProjectTagModel.id != exclude_tag_id)
        return self._session.query(query.exists()).scalar()

    # ------------------------------------------------------------------
    # LaborCostReaderPort
    # ------------------------------------------------------------------

    def sum_labor_cost_by_tag(self, project_id: UUID) -> dict[Optional[UUID], tuple[Decimal, int]]:
        """Return {tag_id: (total_effective_cost, entry_count)} for project's labor entries.

        Mirrors the effective_cost SQL expression from SQLAlchemyLaborEntryRepository.
        labor_entries has no project_id column — must join via workers.
        Supplement-only rows (shift_type IS NULL) contribute 0 cost but are counted.
        """
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

        rows = (
            self._session.query(
                LaborEntryModel.tag_id.label("tag_id"),
                func.sum(effective_cost).label("labor_cost"),
                func.count(LaborEntryModel.id).label("entry_count"),
            )
            .join(WorkerModel, WorkerModel.id == LaborEntryModel.worker_id)
            .filter(WorkerModel.project_id == project_id)
            .group_by(LaborEntryModel.tag_id)
            .all()
        )

        result: dict[Optional[UUID], tuple[Decimal, int]] = {}
        for row in rows:
            cost = Decimal(str(row.labor_cost)) if row.labor_cost is not None else Decimal("0")
            result[row.tag_id] = (cost, int(row.entry_count))
        return result

    # ------------------------------------------------------------------
    # ExpenseTotalReaderPort
    # ------------------------------------------------------------------

    def sum_expense_by_tag(self, project_id: UUID) -> dict[Optional[UUID], tuple[Decimal, int]]:
        """Return {tag_id: (total_invoice_amount, invoice_count)} for project's invoices.

        Invoice total_amount = sum of (quantity * unit_price) across JSON items.
        We load the raw items JSON in Python (same pattern as existing invoice aggregation)
        because JSONB item arithmetic varies by dialect; avoids SQL dialect coupling.
        """
        rows = (
            self._session.query(
                InvoiceModel.tag_id,
                InvoiceModel.items,
            )
            .filter(InvoiceModel.project_id == project_id)
            .all()
        )

        result: dict[Optional[UUID], tuple[Decimal, int]] = {}
        for row in rows:
            tag_id = row.tag_id
            items = row.items or []
            total = Decimal("0")
            for item in items:
                qty = Decimal(str(item.get("quantity", 0)))
                price = Decimal(str(item.get("unit_price", 0)))
                total += qty * price

            if tag_id not in result:
                result[tag_id] = (Decimal("0"), 0)
            existing_total, existing_count = result[tag_id]
            result[tag_id] = (existing_total + total, existing_count + 1)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _to_entity(self, orm: ProjectTagModel) -> ProjectTag:
        return ProjectTag(
            id=orm.id,
            project_id=orm.project_id,
            name=orm.name,
            color=orm.color,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )
