"""User repository port - interface for user persistence."""

from typing import Optional, Protocol, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.domain.entities.user import User


class UserRepositoryPort(Protocol):
    """Port for user persistence operations."""

    def find_by_id(self, user_id: UUID) -> Optional["User"]:
        """Find a user by ID. Returns user or None."""
        ...

    def find_by_email(self, email: str) -> Optional["User"]:
        """Find a user by email. Returns user or None."""
        ...

    def save(self, user: "User") -> "User":
        """Save a user (create or update). Returns saved user."""
        ...
