"""UpdateLaborRoleUseCase — update an existing labor role."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.entities.labor_role import LaborRole
from app.domain.exceptions.labor_exceptions import (
    DuplicateLaborRoleError,
    LaborRoleNotFoundError,
)


class UpdateLaborRoleUseCase:
    """Update name and/or color of an existing labor role.

    Only fields passed as non-None are applied. Name uniqueness is checked
    only when the name actually changes (self-collision excluded).
    """

    def __init__(self, repo: ILaborRoleRepository, db_session: object) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        role_id: UUID,
        name: Optional[str] = None,
        color: Optional[str] = None,
    ) -> LaborRole:
        """Apply updates and persist.

        Raises:
            LaborRoleNotFoundError: role_id does not exist.
            DuplicateLaborRoleError: new name conflicts with another role.
            ValueError: entity-level validation fails (empty name, bad hex).
        """
        role = self._repo.find_by_id(role_id)
        if role is None:
            raise LaborRoleNotFoundError(role_id)

        new_name = name if name is not None else role.name
        new_color = color if color is not None else role.color

        if name is not None and name != role.name:
            conflict = self._repo.find_by_name(name)
            if conflict is not None:
                raise DuplicateLaborRoleError(name)

        updated = LaborRole(
            id=role.id,
            name=new_name,
            color=new_color,
            created_at=role.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        saved = self._repo.update(updated)
        self._db.commit()
        return saved
