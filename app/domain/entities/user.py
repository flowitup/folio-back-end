"""User domain entity."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

# Simple email regex - validates basic format
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class InvalidEmailError(ValueError):
    """Raised when email format is invalid."""

    pass


@dataclass(slots=True)
class User:
    """
    User entity representing an authenticated user.

    Identity only: what a user may do is resolved per request from their
    company role and grant/deny rows, never stored on the user.
    """

    id: UUID
    email: str
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    display_name: Optional[str] = None
    # E.164 number for SMS-code sign-in; assigned by an admin, unique across users.
    phone: Optional[str] = None
    # Platform-ops (flowitup support) bypass — a hidden flag, not a role.
    is_platform_ops: bool = False

    def __eq__(self, other: object) -> bool:
        """Users are equal if they have the same ID."""
        if not isinstance(other, User):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by ID for use in sets/dicts."""
        return hash(self.id)

    @property
    def display_or_email(self) -> str:
        """Return display_name if set, otherwise the local part of the email address."""
        return self.display_name or self.email.split("@")[0]

    @classmethod
    def create(
        cls,
        email: str,
        display_name: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> "User":
        """
        Factory method to create a new User.

        No password is collected: every account authenticates by phone + SMS code.

        Args:
            email: User's email address (validated for format)
            display_name: Optional human-readable name shown in the UI
            phone: Optional E.164 number for SMS-code sign-in

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
            is_active=True,
            created_at=now,
            updated_at=now,
            display_name=display_name,
            phone=phone,
        )
