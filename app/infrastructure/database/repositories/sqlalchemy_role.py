"""SQLAlchemy implementation of RoleRepositoryPort."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.entities.permission import Permission
from app.domain.entities.role import Role
from app.infrastructure.database.models.role import RoleModel


class SqlAlchemyRoleRepository:
    """SQLAlchemy adapter for Role read operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_id(self, role_id: UUID) -> Optional[Role]:
        """Look up a role by UUID. Returns None if not found."""
        model = self._session.query(RoleModel).filter_by(id=role_id).first()
        return self._to_entity(model) if model else None

    def list_all(self) -> List[Role]:
        """Return all roles."""
        models = self._session.query(RoleModel).order_by(RoleModel.name).all()
        return [self._to_entity(m) for m in models]

    @staticmethod
    def _to_entity(model: RoleModel) -> Role:
        permissions = [
            Permission(id=p.id, name=p.name, resource=p.resource, action=p.action) for p in model.permissions
        ]
        return Role(
            id=model.id,
            name=model.name,
            description=model.description or "",
            created_at=model.created_at,
            permissions=permissions,
        )
