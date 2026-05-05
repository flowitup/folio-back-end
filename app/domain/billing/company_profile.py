"""CompanyProfile domain entity for the billing bounded context.

Represents the issuer's legal/banking information managed in Settings.
One row per user. A snapshot of relevant fields is copied onto each
BillingDocument at create time — edits here do NOT mutate existing docs.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """Immutable snapshot of a user's company / issuer information.

    Fields mirror the company_profile DB table exactly.
    prefix_override (e.g. "FLW") is used by the numbering helper;
    empty string means no prefix.
    """

    user_id: UUID
    legal_name: str
    address: str
    created_at: datetime
    updated_at: datetime
    siret: Optional[str] = None
    tva_number: Optional[str] = None
    iban: Optional[str] = None
    bic: Optional[str] = None
    logo_url: Optional[str] = None
    default_payment_terms: Optional[str] = None
    prefix_override: Optional[str] = field(default=None)  # e.g. "FLW"; None / "" = no prefix

    def with_updates(self, **kwargs: object) -> "CompanyProfile":
        """Return a new CompanyProfile with the given fields replaced.

        All other fields are carried over unchanged (frozen dataclass semantics).
        """
        return dataclasses.replace(self, **kwargs)

    @property
    def effective_prefix(self) -> str:
        """Return the prefix string to pass to next_document_number (empty = no prefix)."""
        return self.prefix_override or ""
