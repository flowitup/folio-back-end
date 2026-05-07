"""Company domain entity for the companies bounded context.

Immutable dataclass representing a shared legal entity managed by admins.
Users attach to companies via invite tokens; billing documents reference
companies at create time via an issuer snapshot (not a live FK read).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Company:
    """Immutable company entity.

    All fields map 1-to-1 to the ``companies`` DB table columns.
    Sensitive fields (siret, tva_number, iban, bic) are masked for
    non-admin callers via ``mask_company`` in masking.py — this entity
    always stores the raw values; masking is applied at the read boundary.
    """

    # --- identity ---
    id: UUID
    legal_name: str

    # --- contact / address ---
    address: str

    # --- legal / financial identifiers (sensitive) ---
    siret: Optional[str]
    tva_number: Optional[str]
    iban: Optional[str]
    bic: Optional[str]

    # --- branding ---
    logo_url: Optional[str]

    # --- billing defaults (moved from company_profile) ---
    default_payment_terms: Optional[str]
    prefix_override: Optional[str]

    # --- audit ---
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    # ------------------------------------------------------------------
    # Mutation helper
    # ------------------------------------------------------------------

    def with_updates(self, **kwargs: object) -> "Company":
        """Return a new Company with the given fields replaced.

        All other fields are carried over unchanged (frozen dataclass semantics).
        """
        return dataclasses.replace(self, **kwargs)

    # ------------------------------------------------------------------
    # Equality + hashing by identity
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Company):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
