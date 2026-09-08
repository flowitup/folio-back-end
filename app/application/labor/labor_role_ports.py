"""Labor role repository port (Protocol)."""

from __future__ import annotations

from typing import List, Optional, Protocol
from uuid import UUID

from app.domain.entities.labor_role import LaborRole


class ILaborRoleRepository(Protocol):
    """Persistence contract for LaborRole aggregates."""

    def create(self, role: LaborRole) -> LaborRole:
        """Persist a new labor role and return it."""
        ...

    def find_by_id(self, role_id: UUID) -> Optional[LaborRole]:
        """Return a role by UUID, or None if not found."""
        ...

    def find_by_name(self, name: str, company_id: Optional[UUID] = None) -> Optional[LaborRole]:
        """Return a role matching *name* exactly within *company_id*'s scope, or None.

        `company_id=None` matches legacy/unscoped rows (`company_id IS NULL`)
        — the pre-Phase-2 default, still exercised by callers that have no
        company context (see `matrix.py`/labor role use cases).
        """
        ...

    def list_all(self, company_id: Optional[UUID] = None) -> List[LaborRole]:
        """Return labor roles ordered by name ASC, scoped to *company_id*.

        `company_id=None` returns legacy/unscoped rows only (`company_id IS
        NULL`) — same convention as `find_by_name`.
        """
        ...

    def update(self, role: LaborRole) -> LaborRole:
        """Persist changes to an existing role and return it."""
        ...

    def delete(self, role_id: UUID) -> bool:
        """Delete a role by UUID. Returns True if a row was deleted."""
        ...
