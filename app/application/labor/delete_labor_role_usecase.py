"""DeleteLaborRoleUseCase — delete an existing labor role."""

from __future__ import annotations

from uuid import UUID

from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.exceptions.labor_exceptions import LaborRoleNotFoundError


class DeleteLaborRoleUseCase:
    """Delete a labor role by ID.

    Workers that reference this role will have their role_id set to NULL
    via the ON DELETE SET NULL FK constraint at the database level.
    """

    def __init__(self, repo: ILaborRoleRepository, db_session: object) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, role_id: UUID) -> None:
        """Delete the role.

        Raises:
            LaborRoleNotFoundError: role_id does not exist.
        """
        role = self._repo.find_by_id(role_id)
        if role is None:
            raise LaborRoleNotFoundError(role_id)

        self._repo.delete(role_id)
        self._db.commit()
