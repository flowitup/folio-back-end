"""ListLaborRolesUseCase — list all labor roles."""

from __future__ import annotations

from typing import List

from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.entities.labor_role import LaborRole


class ListLaborRolesUseCase:
    """Return all labor roles ordered by name."""

    def __init__(self, repo: ILaborRoleRepository) -> None:
        self._repo = repo

    def execute(self) -> List[LaborRole]:
        """Return the full list of labor roles."""
        return self._repo.list_all()
