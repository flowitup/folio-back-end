"""SQLAlchemy implementation of the labor payment note repository."""

from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.labor.ports import ILaborPaymentNoteRepository
from app.domain.entities.labor_payment_note import LaborPaymentNote
from app.infrastructure.database.models.labor_payment_note import LaborPaymentNoteModel


class SQLAlchemyLaborPaymentNoteRepository(ILaborPaymentNoteRepository):
    """SQLAlchemy adapter for labor payment note persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _query_one(self, project_id: UUID, worker_id: UUID, month: date) -> Optional[LaborPaymentNoteModel]:
        return (
            self._session.query(LaborPaymentNoteModel)
            .filter_by(project_id=project_id, worker_id=worker_id, month=month)
            .first()
        )

    def find(self, project_id: UUID, worker_id: UUID, month: date) -> Optional[LaborPaymentNote]:
        model = self._query_one(project_id, worker_id, month)
        return self._to_entity(model) if model else None

    def _update_text(self, model: LaborPaymentNoteModel, note: str) -> LaborPaymentNote:
        model.note = note
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        return self._to_entity(model)

    def upsert(self, entity: LaborPaymentNote) -> LaborPaymentNote:
        existing = self._query_one(entity.project_id, entity.worker_id, entity.month)
        if existing is not None:
            return self._update_text(existing, entity.note)

        model = LaborPaymentNoteModel(
            id=entity.id,
            project_id=entity.project_id,
            worker_id=entity.worker_id,
            month=entity.month,
            note=entity.note,
            created_by=entity.created_by,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        self._session.add(model)
        try:
            self._session.commit()
            return self._to_entity(model)
        except IntegrityError:
            # A concurrent save inserted the same key first; converge to an
            # update instead of surfacing a 500 on a double-submit.
            self._session.rollback()
            existing = self._query_one(entity.project_id, entity.worker_id, entity.month)
            if existing is not None:
                return self._update_text(existing, entity.note)
            raise

    def list_by_project(self, project_id: UUID, month: Optional[date] = None) -> List[LaborPaymentNote]:
        query = self._session.query(LaborPaymentNoteModel).filter(LaborPaymentNoteModel.project_id == project_id)
        if month is not None:
            query = query.filter(LaborPaymentNoteModel.month == month)
        return [self._to_entity(m) for m in query.order_by(LaborPaymentNoteModel.month.asc()).all()]

    def delete(self, project_id: UUID, worker_id: UUID, month: date) -> bool:
        model = self._query_one(project_id, worker_id, month)
        if model is None:
            return False
        self._session.delete(model)
        self._session.commit()
        return True

    def _to_entity(self, model: LaborPaymentNoteModel) -> LaborPaymentNote:
        return LaborPaymentNote(
            id=model.id,
            project_id=model.project_id,
            worker_id=model.worker_id,
            month=model.month,
            note=model.note,
            created_by=model.created_by,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
