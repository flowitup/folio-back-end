"""SQLAlchemy implementation of labor entry repository."""

from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import case as sa_case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.application.labor.ports import (
    CrossProjectConflict,
    CrossProjectConflictEntry,
    ILaborEntryRepository,
    LaborSummaryRow,
    MonthlyLaborSummaryRow,
    MonthlyWorkerSubRow,
)
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import DuplicateEntryError
from app.infrastructure.database.models import (
    LaborEntryModel,
    PersonModel,
    ProjectModel,
    WorkerModel,
)


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
        limit: Optional[int] = None,
    ) -> List[LaborEntry]:
        query = self._session.query(LaborEntryModel).join(WorkerModel).filter(WorkerModel.project_id == project_id)
        if date_from:
            query = query.filter(LaborEntryModel.date >= date_from)
        if date_to:
            query = query.filter(LaborEntryModel.date <= date_to)
        if worker_id:
            query = query.filter(LaborEntryModel.worker_id == worker_id)

        query = query.order_by(LaborEntryModel.date.desc())
        if limit is not None and limit > 0:
            query = query.limit(limit)
        models = query.all()
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
        # Load the entity then session.delete + commit. bulk-query .delete()
        # without commit() leaves the unit-of-work open and the row survives
        # request end (same bug class as PR #29 on project repo).
        entry = self._session.query(LaborEntryModel).filter_by(id=entry_id).first()
        if entry is None:
            return False
        self._session.delete(entry)
        self._session.commit()
        return True

    def get_summary(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        worker_id: Optional[UUID] = None,
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

        # `days_worked` is the SUM of shift_multipliers — a full day is
        # 1.0, a half day is 0.5, overtime is 1.5. This matches how cost
        # is computed (cost / days_worked == daily_rate for non-override
        # entries) and matches what users actually pay for. The previous
        # implementation summed `1` per priced row, which over-counted
        # mixed full + half months by treating a half-day as a full day.
        priced_days = sa_case(
            (LaborEntryModel.shift_type.is_(None), 0),
            else_=shift_multiplier,
        )

        display_name = func.coalesce(PersonModel.name, WorkerModel.name)
        query = (
            self._session.query(
                WorkerModel.id.label("worker_id"),
                display_name.label("worker_name"),
                WorkerModel.daily_rate.label("daily_rate"),
                func.sum(priced_days).label("days_worked"),
                func.sum(effective_cost).label("total_cost"),
                func.sum(LaborEntryModel.supplement_hours).label("banked_hours"),
            )
            .join(LaborEntryModel, WorkerModel.id == LaborEntryModel.worker_id)
            .outerjoin(PersonModel, WorkerModel.person_id == PersonModel.id)
            .filter(WorkerModel.project_id == project_id)
            .group_by(WorkerModel.id, display_name, WorkerModel.daily_rate)
        )

        if date_from:
            query = query.filter(LaborEntryModel.date >= date_from)
        if date_to:
            query = query.filter(LaborEntryModel.date <= date_to)
        if worker_id:
            query = query.filter(WorkerModel.id == worker_id)

        rows = query.order_by(display_name).all()

        return [
            LaborSummaryRow(
                worker_id=row.worker_id,
                worker_name=row.worker_name,
                days_worked=Decimal(str(row.days_worked)) if row.days_worked is not None else Decimal("0"),
                total_cost=Decimal(str(row.total_cost)) if row.total_cost else Decimal("0"),
                banked_hours=int(row.banked_hours) if row.banked_hours else 0,
                daily_rate=Decimal(str(row.daily_rate)) if row.daily_rate else Decimal("0"),
            )
            for row in rows
        ]

    def get_monthly_summary(self, project_id: UUID) -> List[MonthlyLaborSummaryRow]:
        # Same effective_cost expression as get_summary, but rolled up per
        # (year, month, worker) so the response can carry the per-worker
        # breakdown inline alongside the month totals. Supplement-only rows
        # contribute 0 to days_worked and 0 to cost.
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

        # `days_worked` = SUM of shift_multipliers, mirroring effective_cost.
        # A full day adds 1.0, a half day 0.5, overtime 1.5. Keeps the
        # invariant cost / days_worked == daily_rate for non-override rows.
        priced_days = sa_case(
            (LaborEntryModel.shift_type.is_(None), 0),
            else_=shift_multiplier,
        )

        year_expr = func.extract("year", LaborEntryModel.date)
        month_expr = func.extract("month", LaborEntryModel.date)
        display_name = func.coalesce(PersonModel.name, WorkerModel.name)

        query = (
            self._session.query(
                year_expr.label("year"),
                month_expr.label("month"),
                WorkerModel.id.label("worker_id"),
                display_name.label("worker_name"),
                func.sum(priced_days).label("days_worked"),
                func.sum(effective_cost).label("total_cost"),
            )
            .join(WorkerModel, WorkerModel.id == LaborEntryModel.worker_id)
            .outerjoin(PersonModel, WorkerModel.person_id == PersonModel.id)
            .filter(WorkerModel.project_id == project_id)
            .group_by(year_expr, month_expr, WorkerModel.id, display_name)
            .order_by(year_expr.desc(), month_expr.desc(), display_name.asc())
        )

        # Bucket the (year, month, worker) granularity rows back into per-month
        # rows with their `workers` list. The DB sort guarantees that within a
        # (year, month) block the worker sub-rows arrive together, alphabetically
        # by name. Workers who only contributed supplement-only rows (days==0
        # AND cost==0) are skipped — they would otherwise add an empty row.
        buckets: dict[tuple[int, int], MonthlyLaborSummaryRow] = {}
        order: List[tuple[int, int]] = []
        for row in query.all():
            key = (int(row.year), int(row.month))
            cost = Decimal(str(row.total_cost)) if row.total_cost is not None else Decimal("0")
            days = Decimal(str(row.days_worked)) if row.days_worked is not None else Decimal("0")
            if days == 0 and cost == 0:
                continue
            sub = MonthlyWorkerSubRow(
                worker_id=row.worker_id,
                worker_name=row.worker_name,
                days_worked=days,
                total_cost=cost,
            )
            bucket = buckets.get(key)
            if bucket is None:
                bucket = MonthlyLaborSummaryRow(
                    year=key[0],
                    month=key[1],
                    total_days=Decimal("0"),
                    total_cost=Decimal("0"),
                    workers=[],
                )
                buckets[key] = bucket
                order.append(key)
            bucket.workers.append(sub)
            bucket.total_days += days
            bucket.total_cost += cost

        return [buckets[k] for k in order]

    def list_by_project_in_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
    ) -> List[LaborEntry]:
        """List all entries for a project within the inclusive date range.

        Ordered by date ASC, then worker name ASC for deterministic export output.
        """
        models = (
            self._session.query(LaborEntryModel)
            .join(WorkerModel)
            .filter(
                WorkerModel.project_id == project_id,
                LaborEntryModel.date >= date_from,
                LaborEntryModel.date <= date_to,
            )
            .order_by(LaborEntryModel.date.asc(), WorkerModel.name.asc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def find_cross_project_conflicts(
        self,
        project_id: UUID,
        date: date,
        person_ids: Optional[List[UUID]] = None,
    ) -> List[CrossProjectConflict]:
        """Find same-day entries from other projects in the same company
        (Phase 4 cross-project conflict warn).

        Single-query join: Person → target Worker (this project) → other
        Worker (different project) → LaborEntry (on date) → other Project
        (same company). Active workers only on both sides; company scope
        prevents leakage between orgs.
        """
        TargetWorker = aliased(WorkerModel)
        OtherWorker = aliased(WorkerModel)
        TargetProject = aliased(ProjectModel)
        OtherProject = aliased(ProjectModel)

        query = (
            self._session.query(
                PersonModel.id.label("person_id"),
                PersonModel.name.label("person_name"),
                OtherProject.id.label("other_project_id"),
                OtherProject.name.label("other_project_name"),
                LaborEntryModel.shift_type.label("shift_type"),
                LaborEntryModel.supplement_hours.label("supplement_hours"),
            )
            .join(TargetWorker, TargetWorker.person_id == PersonModel.id)
            .join(TargetProject, TargetProject.id == TargetWorker.project_id)
            .join(OtherWorker, OtherWorker.person_id == PersonModel.id)
            .join(OtherProject, OtherProject.id == OtherWorker.project_id)
            .join(LaborEntryModel, LaborEntryModel.worker_id == OtherWorker.id)
            .filter(
                TargetWorker.project_id == project_id,
                TargetWorker.is_active == True,  # noqa: E712
                OtherWorker.project_id != project_id,
                OtherWorker.is_active == True,  # noqa: E712
                # Same company. Both NULL means orphaned projects — we
                # treat them as separate orgs so NULL never equals NULL.
                OtherProject.company_id == TargetProject.company_id,
                TargetProject.company_id.isnot(None),
                LaborEntryModel.date == date,
            )
            .order_by(PersonModel.name.asc(), OtherProject.name.asc())
        )
        if person_ids:
            query = query.filter(PersonModel.id.in_(person_ids))

        buckets: dict[UUID, CrossProjectConflict] = {}
        order: List[UUID] = []
        for row in query.all():
            person_id = row.person_id
            bucket = buckets.get(person_id)
            if bucket is None:
                bucket = CrossProjectConflict(
                    person_id=person_id,
                    person_name=row.person_name,
                    entries=[],
                )
                buckets[person_id] = bucket
                order.append(person_id)
            bucket.entries.append(
                CrossProjectConflictEntry(
                    project_id=row.other_project_id,
                    project_name=row.other_project_name,
                    shift_type=row.shift_type,
                    supplement_hours=row.supplement_hours or 0,
                )
            )
        return [buckets[k] for k in order]

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
