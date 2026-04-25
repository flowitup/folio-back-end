"""Role domain entity."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from app.domain.entities.permission import Permission


@dataclass(slots=True)
class Role:
    """
    Role entity representing a set of permissions.

    Roles are assigned to users and define their access rights.
    """

    id: UUID
    name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    permissions: List[Permission] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        """Roles are equal if they have the same ID."""
        if not isinstance(other, Role):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by ID for use in sets/dicts."""
        return hash(self.id)

    @classmethod
    def create(cls, name: str, description: Optional[str] = None) -> "Role":
        """Factory method to create a new Role."""
        return cls(
            id=uuid4(),
            name=name.lower(),
            description=description,
            created_at=datetime.now(timezone.utc),
            permissions=[],
        )

    def add_permission(self, permission: Permission) -> None:
        """Add a permission to this role."""
        if permission not in self.permissions:
            self.permissions.append(permission)

    def has_permission(self, resource: str, action: str) -> bool:
        """Check if this role has the specified permission."""
        return any(p.matches(resource, action) for p in self.permissions)
