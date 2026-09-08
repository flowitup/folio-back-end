"""ListLaborRolesUseCase — list labor roles for a company."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.entities.labor_role import LaborRole


class ListLaborRolesUseCase:
    """Return labor roles ordered by name, scoped to a company (Phase 2)."""

    def __init__(self, repo: ILaborRoleRepository) -> None:
        self._repo = repo

    def execute(self, company_id: Optional[UUID] = None) -> List[LaborRole]:
        """Return the list of labor roles in *company_id*'s scope.

        `company_id=None` returns legacy/unscoped rows only — the
        pre-Phase-2 default for callers with no resolvable company.
        """
        return self._repo.list_all(company_id=company_id)
