"""CompanyPerson domain entity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class CompanyPerson:
    """Company-scoped profile of a global `Person` (Phase 2).

    Identity (name, phone) lives on `Person`; this entity carries the
    company-specific parts: default pay rate, labor role, active/pending
    onboarding state. One row per (company_id, person_id).
    """

    id: UUID
    company_id: UUID
    person_id: UUID
    created_at: datetime
    labor_role_id: Optional[UUID] = None
    default_daily_rate: Optional[Decimal] = None
    is_active: bool = True
    phone_normalized: Optional[str] = None
    pending_expires_at: Optional[datetime] = None
    created_by_user_id: Optional[UUID] = None

    def is_pending(self, now: datetime) -> bool:
        """Return True if this profile was created ahead of a signed-up user
        and that grace window has not yet expired."""
        return self.pending_expires_at is not None and self.pending_expires_at > now

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CompanyPerson):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
