"""SQLAlchemy implementation of labor activity repository."""

from datetime import date
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.labor.ports import ILaborActivityRepository
from app.domain.entities.labor_activity import LaborActivity
from app.infrastructure.database.models.labor_activity import LaborActivityModel


class SQLAlchemyLaborActivityRepository(ILaborActivityRepository):
    """SQLAlchemy adapter for labor activity persistence."""

    def __init__(self, session: Session):
        self._session = session

    def create(self, activity: LaborActivity) -> LaborActivity:
        model = LaborActivityModel(
            id=activity.id,
            project_id=activity.project_id,
            date=activity.date,
            title=activity.title,
            description=activity.description,
            created_by=activity.created_by,
            created_at=activity.created_at,
            updated_at=activity.updated_at,
        )
        self._session.add(model)
        self._session.commit()
        return self._to_entity(model)

    def find_by_id(self, activity_id: UUID) -> Optional[LaborActivity]:
        model = self._session.query(LaborActivityModel).filter_by(id=activity_id).first()
        return self._to_entity(model) if model else None

    def list_by_project(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[LaborActivity]:
        query = self._session.query(LaborActivityModel).filter(
            LaborActivityModel.project_id == project_id,
        )
        if date_from:
            query = query.filter(LaborActivityModel.date >= date_from)
        if date_to:
            query = query.filter(LaborActivityModel.date <= date_to)
        query = query.order_by(LaborActivityModel.date.desc(), LaborActivityModel.created_at.desc())
        return [self._to_entity(m) for m in query.all()]

    def update(self, activity: LaborActivity) -> LaborActivity:
        model = self._session.query(LaborActivityModel).filter_by(id=activity.id).first()
        if model:
            model.title = activity.title
            model.description = activity.description
            model.updated_at = activity.updated_at
            self._session.commit()
            return self._to_entity(model)
        return activity

    def delete(self, activity_id: UUID) -> bool:
        model = self._session.query(LaborActivityModel).filter_by(id=activity_id).first()
        if model is None:
            return False
        self._session.delete(model)
        self._session.commit()
        return True

    def _to_entity(self, model: LaborActivityModel) -> LaborActivity:
        return LaborActivity(
            id=model.id,
            project_id=model.project_id,
            date=model.date,
            title=model.title,
            description=model.description,
            created_by=model.created_by,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
