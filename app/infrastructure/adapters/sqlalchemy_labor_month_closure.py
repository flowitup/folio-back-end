"""SQLAlchemy implementation of the closed labor months repository."""

from datetime import date
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.labor.ports import ILaborMonthClosureRepository
from app.domain.entities.labor_month_closure import LaborMonthClosure, LaborMonthClosureEdit
from app.infrastructure.database.models.labor_month_closure import (
    LaborMonthClosureEditModel,
    LaborMonthClosureModel,
)


class SQLAlchemyLaborMonthClosureRepository(ILaborMonthClosureRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _query_one(self, project_id: UUID, month: date) -> Optional[LaborMonthClosureModel]:
        return self._session.query(LaborMonthClosureModel).filter_by(project_id=project_id, month=month).first()

    def find(self, project_id: UUID, month: date) -> Optional[LaborMonthClosure]:
        model = self._query_one(project_id, month)
        return self._to_entity(model) if model else None

    def list_by_project(self, project_id: UUID, months: Optional[List[date]] = None) -> List[LaborMonthClosure]:
        query = self._session.query(LaborMonthClosureModel).filter(LaborMonthClosureModel.project_id == project_id)
        if months is not None:
            if not months:
                return []
            query = query.filter(LaborMonthClosureModel.month.in_(list(months)))
        return [self._to_entity(m) for m in query.order_by(LaborMonthClosureModel.month.asc()).all()]

    def create(self, closure: LaborMonthClosure) -> LaborMonthClosure:
        model = LaborMonthClosureModel(
            id=closure.id,
            project_id=closure.project_id,
            month=closure.month,
            closed_by=closure.closed_by,
            closed_at=closure.closed_at,
        )
        self._session.add(model)
        try:
            self._session.commit()
        except IntegrityError:
            # A concurrent close of the same month won: converge on that row.
            self._session.rollback()
            existing = self._query_one(closure.project_id, closure.month)
            if existing is not None:
                return self._to_entity(existing)
            raise
        return self._to_entity(model)

    def delete(self, project_id: UUID, month: date) -> bool:
        model = self._query_one(project_id, month)
        if model is None:
            return False
        # Explicit, so SQLite (tests) drops the edits too without ON DELETE CASCADE.
        self._session.query(LaborMonthClosureEditModel).filter(
            LaborMonthClosureEditModel.closure_id == model.id
        ).delete(synchronize_session=False)
        self._session.delete(model)
        self._session.commit()
        return True

    def add_edits(self, edits: List[LaborMonthClosureEdit]) -> None:
        if not edits:
            return
        self._session.add_all(
            [
                LaborMonthClosureEditModel(
                    id=e.id,
                    closure_id=e.closure_id,
                    user_id=e.user_id,
                    edited_at=e.edited_at,
                    kind=e.kind,
                    entry_id=e.entry_id,
                    worker_id=e.worker_id,
                    entry_date=e.entry_date,
                )
                for e in edits
            ]
        )
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def list_edits(self, closure_ids: List[UUID]) -> Dict[UUID, List[LaborMonthClosureEdit]]:
        out: Dict[UUID, List[LaborMonthClosureEdit]] = {cid: [] for cid in closure_ids}
        if not closure_ids:
            return out
        rows = (
            self._session.query(LaborMonthClosureEditModel)
            .filter(LaborMonthClosureEditModel.closure_id.in_(list(closure_ids)))
            .order_by(LaborMonthClosureEditModel.edited_at.asc())
            .all()
        )
        for m in rows:
            out.setdefault(m.closure_id, []).append(
                LaborMonthClosureEdit(
                    id=m.id,
                    closure_id=m.closure_id,
                    kind=m.kind,
                    edited_at=m.edited_at,
                    user_id=m.user_id,
                    entry_id=m.entry_id,
                    worker_id=m.worker_id,
                    entry_date=m.entry_date,
                )
            )
        return out

    def user_names(self, user_ids: List[UUID]) -> Dict[UUID, str]:
        from app.infrastructure.database.models import UserModel

        if not user_ids:
            return {}
        rows = (
            self._session.query(UserModel.id, UserModel.display_name, UserModel.phone, UserModel.email)
            .filter(UserModel.id.in_(list(user_ids)))
            .all()
        )
        out: Dict[UUID, str] = {}
        for uid, display_name, phone, email in rows:
            label = (display_name or "").strip() or (phone or "").strip() or (email or "").strip()
            if label:
                out[uid] = label
        return out

    def worker_names(self, worker_ids: List[UUID]) -> Dict[UUID, str]:
        from app.infrastructure.database.models import PersonModel, WorkerModel

        if not worker_ids:
            return {}
        rows = (
            self._session.query(WorkerModel.id, WorkerModel.name, PersonModel.name)
            .outerjoin(PersonModel, PersonModel.id == WorkerModel.person_id)
            .filter(WorkerModel.id.in_(list(worker_ids)))
            .all()
        )
        return {wid: (person_name or name) for wid, name, person_name in rows if (person_name or name)}

    @staticmethod
    def _to_entity(model: LaborMonthClosureModel) -> LaborMonthClosure:
        return LaborMonthClosure(
            id=model.id,
            project_id=model.project_id,
            month=model.month,
            closed_at=model.closed_at,
            closed_by=model.closed_by,
        )
