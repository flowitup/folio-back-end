"""CompanyInviteToken domain entity for the companies bounded context.

Single-use invite tokens that allow users to attach themselves to a company.
The plaintext token is shown ONCE at generation time; only the argon2 hash
is persisted. Verification happens in the application layer via an injected
port — this entity only stores the hash string.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CompanyInviteToken:
    """Immutable invite-token entity.

    Fields map 1-to-1 to the ``company_invite_tokens`` DB table columns.

    Lifecycle:
      active  → is_active(now) == True   (not expired, not redeemed)
      expired → is_expired(now) == True  (past expires_at, not redeemed)
      redeemed → is_redeemed == True     (redeemed_at set, redeemed_by set)

    The token_hash stores the argon2 digest of the plaintext token.
    Verification is performed by the application layer; this entity is
    purely a value carrier.
    """

    # --- identity ---
    id: UUID
    company_id: UUID

    # --- token storage (argon2 hash of plaintext) ---
    token_hash: str

    # --- audit ---
    created_by: UUID
    created_at: datetime
    expires_at: datetime

    # --- redemption (None = not yet redeemed) ---
    redeemed_at: Optional[datetime]
    redeemed_by: Optional[UUID]

    # ------------------------------------------------------------------
    # Computed state properties
    # ------------------------------------------------------------------

    def is_expired(self, now: datetime) -> bool:
        """Return True if the token has passed its expiry timestamp."""
        return now >= self.expires_at

    @property
    def is_redeemed(self) -> bool:
        """Return True if the token has already been redeemed."""
        return self.redeemed_at is not None

    def is_active(self, now: datetime) -> bool:
        """Return True if the token can still be used to attach a user.

        A token is active when it has not expired AND has not been redeemed.
        """
        return not self.is_expired(now) and not self.is_redeemed

    # ------------------------------------------------------------------
    # Mutation helper
    # ------------------------------------------------------------------

    def with_updates(self, **kwargs: object) -> "CompanyInviteToken":
        """Return a new CompanyInviteToken with the given fields replaced.

        All other fields are carried over unchanged (frozen dataclass semantics).
        """
        return dataclasses.replace(self, **kwargs)

    # ------------------------------------------------------------------
    # Equality + hashing by identity
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CompanyInviteToken):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
