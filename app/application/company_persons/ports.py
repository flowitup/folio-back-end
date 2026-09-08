"""Repository port for the company_persons bounded context."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Protocol
from uuid import UUID

from app.domain.entities.company_person import CompanyPerson


class CompanyPersonRepositoryPort(Protocol):
    """Persistence contract for CompanyPerson aggregates."""

    def find(self, company_id: UUID, person_id: UUID) -> Optional[CompanyPerson]:
        """Return the profile for (company_id, person_id), or None."""
        ...

    def find_by_phone(self, company_id: UUID, phone_normalized: str) -> Optional[CompanyPerson]:
        """Return the profile matching an exact normalized phone WITHIN one company, or None.

        The partial unique index on (company_id, phone_normalized) guarantees
        at most one active match per company — callers implementing
        add-by-phone matching (Phase 2 onboarding slice) still need to check
        for a Person-level match across companies separately.
        """
        ...

    def list_for_company(self, company_id: UUID, include_inactive: bool = False) -> List[CompanyPerson]:
        """Return every profile in a company, ordered by creation date.

        `include_inactive=False` (default) filters out booted/deactivated
        rows — the roster and directory only ever show active members.
        """
        ...

    def list_for_person(self, person_id: UUID) -> List[CompanyPerson]:
        """Return every company profile of a Person (their companies)."""
        ...

    def save(self, profile: CompanyPerson) -> CompanyPerson:
        """Insert or update a profile. Returns the persisted instance."""
        ...

    def deactivate(self, company_id: UUID, person_id: UUID) -> bool:
        """Set is_active=False for a profile (boot/detach). Returns True if a row was updated."""
        ...

    def list_pending_by_phone(self, phone_normalized: str, now: datetime) -> List[CompanyPerson]:
        """Return every non-expired pending profile matching a normalized phone.

        Used by sign-up linking (Phase 2 onboarding slice): a fresh account
        verified at this phone number should attach to every company that
        has an admin-created, still-open pending profile for it.
        """
        ...
