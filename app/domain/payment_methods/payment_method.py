"""PaymentMethod domain entity for the payment_methods bounded context.

Immutable dataclass representing a reusable payment method owned by a company.
Soft-delete is modelled via ``is_active``; builtin rows (Cash, company name)
are protected from deletion via ``is_builtin``.

No infrastructure or Flask imports are permitted in this module.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PaymentMethod:
    """Immutable payment method entity.

    All fields map 1-to-1 to the ``payment_methods`` DB table columns.
    Equality and hashing are identity-based (``id`` field only).
    """

    # --- identity ---
    id: UUID
    company_id: UUID

    # --- display ---
    label: str

    # --- flags ---
    is_builtin: bool  # Cash / company-name rows seeded by migration + create-company hook
    is_active: bool  # False = soft-deleted; unique constraint is partial (active only)

    # --- audit ---
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    # ------------------------------------------------------------------
    # Mutation helper
    # ------------------------------------------------------------------

    def with_updates(
        self,
        *,
        label: Optional[str] = None,
        is_active: Optional[bool] = None,
        updated_at: Optional[datetime] = None,
    ) -> "PaymentMethod":
        """Return a new PaymentMethod with the given fields replaced.

        Only the supplied (non-None) keyword arguments are changed;
        all other fields are carried over unchanged (frozen dataclass semantics).
        """
        kwargs: dict = {}
        if label is not None:
            kwargs["label"] = label
        if is_active is not None:
            kwargs["is_active"] = is_active
        if updated_at is not None:
            kwargs["updated_at"] = updated_at
        return dataclasses.replace(self, **kwargs)

    # ------------------------------------------------------------------
    # Equality + hashing by identity
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PaymentMethod):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
