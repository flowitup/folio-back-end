"""UserCompanyAccess domain entity for the companies bounded context.

Represents the many-to-many join between a user and a company.
One access row per (user_id, company_id) pair; at most one row per user
may have is_primary=True (enforced by a partial unique index in the DB).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UserCompanyAccess:
    """Immutable join entity linking a user to an attached company.

    Fields map 1-to-1 to the ``user_company_access`` DB table columns.
    is_primary=True means this company is used as the default issuer when
    creating billing documents and as fallback when no company_id is given.
    """

    # --- foreign keys ---
    user_id: UUID
    company_id: UUID

    # --- primary flag ---
    is_primary: bool

    # --- audit ---
    attached_at: datetime

    # ------------------------------------------------------------------
    # Mutation helper
    # ------------------------------------------------------------------

    def with_updates(self, **kwargs: object) -> "UserCompanyAccess":
        """Return a new UserCompanyAccess with the given fields replaced.

        All other fields are carried over unchanged (frozen dataclass semantics).
        """
        return dataclasses.replace(self, **kwargs)

    # ------------------------------------------------------------------
    # Equality + hashing by composite key
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserCompanyAccess):
            return NotImplemented
        return self.user_id == other.user_id and self.company_id == other.company_id

    def __hash__(self) -> int:
        return hash((self.user_id, self.company_id))
