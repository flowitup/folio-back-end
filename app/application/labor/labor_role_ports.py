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

    def find_by_name(self, name: str) -> Optional[LaborRole]:
        """Return a role matching *name* exactly (case-sensitive), or None."""
        ...

    def list_all(self) -> List[LaborRole]:
        """Return all labor roles ordered by name ASC."""
        ...

    def update(self, role: LaborRole) -> LaborRole:
        """Persist changes to an existing role and return it."""
        ...

    def delete(self, role_id: UUID) -> bool:
        """Delete a role by UUID. Returns True if a row was deleted."""
        ...
