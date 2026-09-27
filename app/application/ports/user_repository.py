"""User repository port - interface for user persistence."""

from typing import Optional, Protocol, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from datetime import datetime

    from app.domain.entities.user import User


class UserRepositoryPort(Protocol):
    """Port for user persistence operations."""

    def find_by_id(self, user_id: UUID) -> Optional["User"]:
        """Find a user by ID. Returns user or None."""
        ...

    def find_by_email(self, email: str) -> Optional["User"]:
        """Find a user by email. Returns user or None."""
        ...

    def find_by_phone(self, phone: str) -> Optional["User"]:
        """Find a user by E.164 phone number. Returns user or None."""
        ...

    def is_sign_in_allowed(self, user_id: UUID, issued_at: Optional[int] = None) -> bool:
        """True when this user still exists, is active, and has not been erased.

        With ``issued_at`` (a token's ``iat``, epoch seconds), also False when the token predates
        the user's sessions cut-off (``end_sessions_issued_before``).

        Deliberately not expressed as ``find_by_id(...).is_active``: this is
        checked on every authenticated request, so it must read the columns
        straight from the database rather than whatever the ORM session has
        cached, and must not add the user to the identity map.
        """
        ...

    def end_sessions_issued_before(self, user_id: UUID, cutoff: "datetime") -> None:
        """Refuse from now on every token of this user issued before ``cutoff`` (whole seconds)."""
        ...

    def save(self, user: "User") -> "User":
        """Save a user (create or update). Returns saved user."""
        ...

    def assign_role(self, user_id: UUID, role_id: UUID) -> None:
        """Grant a global role to a user (no-op when already granted)."""
        ...
