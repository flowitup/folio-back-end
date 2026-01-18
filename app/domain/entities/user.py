"""User domain entity."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from app.domain.entities.role import Role

# Simple email regex - validates basic format
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class InvalidEmailError(ValueError):
    """Raised when email format is invalid."""
    pass


@dataclass(slots=True)
class User:
    """
    User entity representing an authenticated user.

    Users have email/password credentials and assigned roles.
    """
    id: UUID
    email: str
    password_hash: str
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    roles: List[Role] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        """Users are equal if they have the same ID."""
        if not isinstance(other, User):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by ID for use in sets/dicts."""
        return hash(self.id)

    @classmethod
    def create(cls, email: str, password_hash: str) -> "User":
        """
        Factory method to create a new User.

        Args:
            email: User's email address (validated for format)
            password_hash: Pre-hashed password

        Returns:
            New User instance

        Raises:
            InvalidEmailError: If email format is invalid
        """
        email = email.lower().strip()
        if not EMAIL_REGEX.match(email):
            raise InvalidEmailError(f"Invalid email format: {email}")

        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            email=email,
            password_hash=password_hash,
            is_active=True,
            created_at=now,
            updated_at=now,
            roles=[],
        )

    def add_role(self, role: Role) -> None:
        """Assign a role to this user."""
        if role not in self.roles:
            self.roles.append(role)

    def remove_role(self, role: Role) -> None:
        """Remove a role from this user."""
        if role in self.roles:
            self.roles.remove(role)

    def has_permission(self, resource: str, action: str) -> bool:
        """Check if user has permission through any of their roles."""
        return any(role.has_permission(resource, action) for role in self.roles)

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role by name."""
        return any(r.name == role_name.lower() for r in self.roles)
