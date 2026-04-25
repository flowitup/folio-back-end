"""Permission domain entity."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


@dataclass(slots=True)
class Permission:
    """
    Permission entity representing an action on a resource.

    Format: resource:action (e.g., 'project:create')
    """

    id: UUID
    name: str  # Full permission name: 'resource:action'
    resource: str  # Resource type: 'project', 'user', etc.
    action: str  # Action type: 'create', 'read', 'update', 'delete'
    created_at: Optional[datetime] = None

    def __eq__(self, other: object) -> bool:
        """Permissions are equal if they have the same ID."""
        if not isinstance(other, Permission):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by ID for use in sets/dicts."""
        return hash(self.id)

    @classmethod
    def create(cls, resource: str, action: str) -> "Permission":
        """Factory method to create a new Permission."""
        return cls(
            id=uuid4(),
            name=f"{resource}:{action}",
            resource=resource,
            action=action,
            created_at=datetime.now(timezone.utc),
        )

    def matches(self, resource: str, action: str) -> bool:
        """Check if this permission matches the given resource and action."""
        # Wildcard support: '*' matches any
        if self.resource == "*" or self.resource == resource:
            if self.action == "*" or self.action == action:
                return True
        return False
