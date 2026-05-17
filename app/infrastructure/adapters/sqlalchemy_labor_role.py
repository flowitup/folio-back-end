"""SQLAlchemy implementation of the labor role repository."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.entities.labor_role import LaborRole
from app.infrastructure.database.models.labor_role import LaborRoleModel


class SQLAlchemyLaborRoleRepository(ILaborRoleRepository):
    """SQLAlchemy adapter for LaborRole persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create(self, role: LaborRole) -> LaborRole:
        model = LaborRoleModel(
            id=role.id,
            name=role.name,
            color=role.color,
            created_at=role.created_at,
        )
        self._session.add(model)
        self._session.flush()
        return self._to_entity(model)

    def update(self, role: LaborRole) -> LaborRole:
        model = self._session.query(LaborRoleModel).filter_by(id=role.id).first()
        if model:
            model.name = role.name
            model.color = role.color
            model.updated_at = role.updated_at
            self._session.flush()
            return self._to_entity(model)
        return role

    def delete(self, role_id: UUID) -> bool:
        deleted = self._session.query(LaborRoleModel).filter_by(id=role_id).delete(synchronize_session=False)
        return deleted > 0

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def find_by_id(self, role_id: UUID) -> Optional[LaborRole]:
        model = self._session.query(LaborRoleModel).filter_by(id=role_id).first()
        return self._to_entity(model) if model else None

    def find_by_name(self, name: str) -> Optional[LaborRole]:
        model = self._session.query(LaborRoleModel).filter_by(name=name).first()
        return self._to_entity(model) if model else None

    def list_all(self) -> List[LaborRole]:
        models = self._session.query(LaborRoleModel).order_by(LaborRoleModel.name).all()
        return [self._to_entity(m) for m in models]

    # ------------------------------------------------------------------
    # Mapper
    # ------------------------------------------------------------------

    @staticmethod
    def _to_entity(model: LaborRoleModel) -> LaborRole:
        return LaborRole(
            id=model.id,
            name=model.name,
            color=model.color,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
