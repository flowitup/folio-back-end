"""SQLAlchemy implementation of labor day description repository."""

from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.labor.ports import ILaborDayDescriptionRepository
from app.domain.entities.labor_day_description import LaborDayDescription
from app.infrastructure.database.models.labor_day_description import LaborDayDescriptionModel


class SQLAlchemyLaborDayDescriptionRepository(ILaborDayDescriptionRepository):
    """SQLAlchemy adapter for labor day description persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_project_and_date(self, project_id: UUID, description_date: date) -> Optional[LaborDayDescription]:
        """Return the single description for (project_id, date), or None if absent."""
        model = (
            self._session.query(LaborDayDescriptionModel)
            .filter_by(project_id=project_id, date=description_date)
            .first()
        )
        return self._to_entity(model) if model else None

    def upsert(self, entity: LaborDayDescription) -> LaborDayDescription:
        """Insert or update the description row keyed by (project_id, date).

        Checks for an existing row first. If found, updates description and
        updated_at in place (preserving created_by and created_at). Otherwise
        inserts a fresh row.
        """
        existing = (
            self._session.query(LaborDayDescriptionModel)
            .filter_by(project_id=entity.project_id, date=entity.date)
            .first()
        )
        if existing is not None:
            existing.description = entity.description
            existing.updated_at = datetime.now(timezone.utc)
            self._session.commit()
            return self._to_entity(existing)

        model = LaborDayDescriptionModel(
            id=entity.id,
            project_id=entity.project_id,
            date=entity.date,
            description=entity.description,
            created_by=entity.created_by,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
        self._session.add(model)
        self._session.commit()
        return self._to_entity(model)

    def list_by_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
    ) -> List[LaborDayDescription]:
        """List descriptions for a project within the inclusive date range, ordered by date ASC."""
        query = (
            self._session.query(LaborDayDescriptionModel)
            .filter(
                LaborDayDescriptionModel.project_id == project_id,
                LaborDayDescriptionModel.date >= date_from,
                LaborDayDescriptionModel.date <= date_to,
            )
            .order_by(LaborDayDescriptionModel.date.asc())
        )
        return [self._to_entity(m) for m in query.all()]

    def delete_by_date(self, project_id: UUID, description_date: date) -> bool:
        """Delete the description for (project_id, date). Returns True if deleted."""
        model = (
            self._session.query(LaborDayDescriptionModel)
            .filter_by(project_id=project_id, date=description_date)
            .first()
        )
        if model is None:
            return False
        self._session.delete(model)
        self._session.commit()
        return True

    def _to_entity(self, model: LaborDayDescriptionModel) -> LaborDayDescription:
        return LaborDayDescription(
            id=model.id,
            project_id=model.project_id,
            date=model.date,
            description=model.description,
            created_by=model.created_by,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
