"""SQLAlchemy implementation of labor entry repository."""

from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import case as sa_case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.labor.ports import ILaborEntryRepository, LaborSummaryRow
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import DuplicateEntryError
from app.infrastructure.database.models import LaborEntryModel, WorkerModel


class SQLAlchemyLaborEntryRepository(ILaborEntryRepository):
    """SQLAlchemy adapter for labor entry persistence."""

    def __init__(self, session: Session):
        self._session = session

    def create(self, entry: LaborEntry) -> LaborEntry:
        model = LaborEntryModel(
            id=entry.id,
            worker_id=entry.worker_id,
            date=entry.date,
            amount_override=entry.amount_override,
            note=entry.note,
            shift_type=entry.shift_type,
            supplement_hours=entry.supplement_hours,
            created_at=entry.created_at,
        )
        try:
            self._session.add(model)
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raise DuplicateEntryError(str(entry.worker_id), entry.date.isoformat())
        return self._to_entity(model)

    def find_by_id(self, entry_id: UUID) -> Optional[LaborEntry]:
        model = self._session.query(LaborEntryModel).filter_by(id=entry_id).first()
        return self._to_entity(model) if model else None

    def list_by_project(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        worker_id: Optional[UUID] = None,
    ) -> List[LaborEntry]:
        query = self._session.query(LaborEntryModel).join(WorkerModel).filter(WorkerModel.project_id == project_id)
        if date_from:
            query = query.filter(LaborEntryModel.date >= date_from)
        if date_to:
            query = query.filter(LaborEntryModel.date <= date_to)
        if worker_id:
            query = query.filter(LaborEntryModel.worker_id == worker_id)

        models = query.order_by(LaborEntryModel.date.desc()).all()
        return [self._to_entity(m) for m in models]

    def update(self, entry: LaborEntry) -> LaborEntry:
        model = self._session.query(LaborEntryModel).filter_by(id=entry.id).first()
        if model:
            model.amount_override = entry.amount_override
            model.note = entry.note
            model.shift_type = entry.shift_type
            model.supplement_hours = entry.supplement_hours
            self._session.flush()
            return self._to_entity(model)
        return entry

    def delete(self, entry_id: UUID) -> bool:
        result = self._session.query(LaborEntryModel).filter_by(id=entry_id).delete()
        return result > 0

    def get_summary(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[LaborSummaryRow]:
        # Effective cost: supplement-only rows (shift_type IS NULL) contribute 0.
        # For shift rows: override wins; else daily_rate × shift multiplier.
        shift_multiplier = sa_case(
            (LaborEntryModel.shift_type == "half", 0.5),
            (LaborEntryModel.shift_type == "overtime", 1.5),
            else_=1.0,
        )
        shift_cost = func.coalesce(
            LaborEntryModel.amount_override,
            WorkerModel.daily_rate * shift_multiplier,
        )
        effective_cost = sa_case(
            (LaborEntryModel.shift_type.is_(None), 0),
            else_=shift_cost,
        )

        query = (
            self._session.query(
                WorkerModel.id.label("worker_id"),
                WorkerModel.name.label("worker_name"),
                func.count(LaborEntryModel.id).label("days_worked"),
                func.sum(effective_cost).label("total_cost"),
                func.sum(LaborEntryModel.supplement_hours).label("banked_hours"),
            )
            .join(LaborEntryModel, WorkerModel.id == LaborEntryModel.worker_id)
            .filter(WorkerModel.project_id == project_id)
            .group_by(WorkerModel.id, WorkerModel.name)
        )

        if date_from:
            query = query.filter(LaborEntryModel.date >= date_from)
        if date_to:
            query = query.filter(LaborEntryModel.date <= date_to)

        rows = query.order_by(WorkerModel.name).all()

        return [
            LaborSummaryRow(
                worker_id=row.worker_id,
                worker_name=row.worker_name,
                days_worked=row.days_worked,
                total_cost=Decimal(str(row.total_cost)) if row.total_cost else Decimal("0"),
                banked_hours=int(row.banked_hours) if row.banked_hours else 0,
            )
            for row in rows
        ]

    def _to_entity(self, model: LaborEntryModel) -> LaborEntry:
        return LaborEntry(
            id=model.id,
            worker_id=model.worker_id,
            date=model.date,
            amount_override=Decimal(str(model.amount_override)) if model.amount_override else None,
            note=model.note,
            shift_type=model.shift_type,  # pass-through; may be None for supplement-only entries
            supplement_hours=model.supplement_hours if model.supplement_hours is not None else 0,
            created_at=model.created_at,
        )
