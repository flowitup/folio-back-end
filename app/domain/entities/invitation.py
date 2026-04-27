"""Invitation domain entity — models the invite lifecycle."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from app.domain.value_objects.invite_token import generate_token
from app.domain.exceptions.invitation_exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationNotUsableError,
    InvitationRevokedError,
)

# Reuse same pattern as User entity
_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class InvalidInvitationEmailError(ValueError):
    """Raised when an invitation email fails format validation."""

    pass


def _normalize_email(email: str) -> str:
    """Strip, lowercase and validate email. Raises InvalidInvitationEmailError on failure."""
    normalized = email.strip().lower()
    if not _EMAIL_REGEX.match(normalized):
        raise InvalidInvitationEmailError(f"Invalid email format: {normalized}")
    return normalized


class InvitationStatus(Enum):
    """Lifecycle states for an invitation."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass
class Invitation:
    """
    Invitation entity.

    Raw token is NEVER stored here — only the sha256 hash.
    State transitions (accept/revoke) return new instances via dataclasses.replace.
    """

    id: UUID
    email: str
    project_id: UUID
    role_id: UUID
    token_hash: str
    status: InvitationStatus
    expires_at: datetime
    invited_by: UUID
    created_at: datetime
    accepted_at: Optional[datetime] = field(default=None)
    updated_at: Optional[datetime] = field(default=None)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        email: str,
        project_id: UUID,
        role_id: UUID,
        invited_by: UUID,
        ttl_days: int = 7,
    ) -> tuple["Invitation", str]:
        """
        Create a new PENDING invitation.

        Args:
            email: Recipient email address (normalized + validated).
            project_id: Target project UUID.
            role_id: Role to grant on acceptance.
            invited_by: UUID of the user sending the invitation.
            ttl_days: Days until the invitation expires (default 7).

        Returns:
            (Invitation, raw_token) — raw_token must be emailed and then discarded;
            only the hash is kept on the entity.
        """
        normalized_email = _normalize_email(email)
        raw_token, token_hash = generate_token()
        now = datetime.now(timezone.utc)

        invitation = cls(
            id=uuid4(),
            email=normalized_email,
            project_id=project_id,
            role_id=role_id,
            token_hash=token_hash,
            status=InvitationStatus.PENDING,
            expires_at=now + timedelta(days=ttl_days),
            invited_by=invited_by,
            created_at=now,
            accepted_at=None,
            updated_at=now,
        )
        return invitation, raw_token

    # ------------------------------------------------------------------
    # Domain queries
    # ------------------------------------------------------------------

    def is_usable(self) -> bool:
        """Return True iff status is PENDING and the invitation has not expired."""
        # SQLite strips tzinfo on load; treat naive datetimes as UTC for the comparison.
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return (
            self.status == InvitationStatus.PENDING
            and datetime.now(timezone.utc) < expires
        )

    # ------------------------------------------------------------------
    # State transitions (immutable-style — return new instances)
    # ------------------------------------------------------------------

    def accept(self) -> "Invitation":
        """
        Transition to ACCEPTED.

        Raises:
            InvitationAlreadyAcceptedError: if already accepted.
            InvitationRevokedError: if revoked.
            InvitationExpiredError: if past expiry.
            InvitationNotUsableError: fallback for any other non-usable state.
        """
        if self.status == InvitationStatus.ACCEPTED:
            raise InvitationAlreadyAcceptedError(
                f"Invitation {self.id} has already been accepted."
            )
        if self.status == InvitationStatus.REVOKED:
            raise InvitationRevokedError(f"Invitation {self.id} has been revoked.")
        if not self.is_usable():
            # Status is PENDING but past expiry, or EXPIRED
            raise InvitationExpiredError(f"Invitation {self.id} has expired.")

        now = datetime.now(timezone.utc)
        from dataclasses import replace  # local import to avoid top-level cycle risk
        return replace(
            self,
            status=InvitationStatus.ACCEPTED,
            accepted_at=now,
            updated_at=now,
        )

    def revoke(self) -> "Invitation":
        """
        Transition to REVOKED.

        Returns a new Invitation with status=REVOKED.
        Does NOT raise — revoking an already-revoked invitation is idempotent.
        """
        now = datetime.now(timezone.utc)
        from dataclasses import replace
        return replace(
            self,
            status=InvitationStatus.REVOKED,
            updated_at=now,
        )
