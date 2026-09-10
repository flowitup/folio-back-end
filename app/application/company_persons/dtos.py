"""DTOs for the company_persons onboarding application layer (Phase 2 slice B)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional
from uuid import UUID


@dataclass(frozen=True)
class AddMemberByPhoneInput:
    """Input for AddMemberByPhoneUseCase (POST /companies/<id>/members)."""

    caller_id: UUID
    company_id: UUID
    phone: str
    name: Optional[str] = None
    role: str = "member"  # member | manager — 'admin' is rejected (400)
    # Disambiguation resend after a 409 MultipleCandidatesError (match order (c)).
    person_id: Optional[UUID] = None


@dataclass(frozen=True)
class AddMemberByPhoneResult:
    """Result of AddMemberByPhoneUseCase.

    Same shape whether the phone matched an existing account or a brand new
    profile was created — the response must never let the caller distinguish
    "this phone already has an account" from "this is a fresh profile"
    (no enumeration, mirrors the sign-up-linking privacy requirement).
    """

    person_id: UUID
    name: str
    phone: str
    pending: bool  # True when no user account is linked yet


@dataclass(frozen=True)
class ImportMembersInput:
    """Input for ImportMembersUseCase (POST /companies/<id>/members/import)."""

    caller_id: UUID
    company_id: UUID
    from_company_id: UUID
    person_ids: List[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class ImportedMember:
    person_id: UUID
    name: str
    phone: Optional[str]
    linked_user_id: Optional[UUID]


@dataclass(frozen=True)
class ImportMembersResult:
    items: List[ImportedMember]
    # person_ids from the request that were skipped: not a member of
    # from_company_id, or the Person row itself no longer exists.
    skipped_person_ids: List[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class DirectoryEntry:
    """One row of GET /companies/<id>/persons."""

    person_id: UUID
    name: str
    phone: Optional[str]
    linked_user_id: Optional[UUID]
    assigned_project_ids: List[UUID]
    is_active: bool
    pending: bool
    labor_role_id: Optional[UUID] = None
    default_daily_rate: Optional[Decimal] = None


@dataclass(frozen=True)
class ListDirectoryResult:
    items: List[DirectoryEntry]


@dataclass(frozen=True)
class UpdateMemberPayDefaultsInput:
    """Input for UpdateMemberPayDefaultsUseCase (PATCH /companies/<id>/members/<person_id>).

    PATCH semantics: a field is written only when its `set_*` flag is True, so
    an absent key leaves the stored value alone while an explicit ``null``
    clears it. Without the flags the two cases would be indistinguishable here
    and clearing a rate would be impossible.
    """

    caller_id: UUID
    company_id: UUID
    person_id: UUID
    default_daily_rate: Optional[Decimal] = None
    labor_role_id: Optional[UUID] = None
    set_default_daily_rate: bool = False
    set_labor_role_id: bool = False


@dataclass(frozen=True)
class MemberPayDefaults:
    """The stored pay defaults after an update."""

    person_id: UUID
    default_daily_rate: Optional[Decimal]
    labor_role_id: Optional[UUID]
