"""CreateLaborRoleUseCase — create a new global labor role."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.entities.labor_role import LaborRole
from app.domain.exceptions.labor_exceptions import DuplicateLaborRoleError


class CreateLaborRoleUseCase:
    """Create a new labor role.

    Validates name uniqueness before inserting. The caller owns the
    transaction boundary: pass a db.session-compatible object as
    ``db_session`` so the use case can commit after a successful insert.
    """

    def __init__(self, repo: ILaborRoleRepository, db_session: object) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, name: str, color: str) -> LaborRole:
        """Create and persist a labor role.

        Raises:
            DuplicateLaborRoleError: a role with *name* already exists.
            ValueError: entity-level validation fails (empty name, bad hex).
        """
        existing = self._repo.find_by_name(name)
        if existing is not None:
            raise DuplicateLaborRoleError(name)

        role = LaborRole(
            id=uuid4(),
            name=name,
            color=color,
            created_at=datetime.now(timezone.utc),
        )
        saved = self._repo.create(role)
        self._db.commit()
        return saved
