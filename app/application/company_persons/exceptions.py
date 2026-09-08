"""Exceptions for the company_persons onboarding application layer.

Kept separate from `app.domain.companies.exceptions` — this bounded context
(admin adds people by phone / from another company) is additive on top of the
companies BC and should not force every existing companies use-case caller to
learn new exception types.
"""

from __future__ import annotations

from typing import List
from uuid import UUID


class CompanyPersonsError(Exception):
    """Base class for all company_persons onboarding errors."""


class AdminRoleNotAssignableError(CompanyPersonsError):
    """Raised when an admin tries to add a member with role='admin'.

    Admin is granted only via company creation or `SetMemberRoleUseCase` on
    an existing member — never as the initial role of an add-by-phone or
    import call (D8-adjacent guard: this endpoint is for onboarding
    manager/member, not for minting new admins by phone).
    """


class MultipleCandidatesError(CompanyPersonsError):
    """Raised when more than one un-linked Person matches the phone (match order (c)).

    The caller (company admin) must resend the request with an explicit
    ``person_id`` picked from ``candidates`` to disambiguate.
    """

    def __init__(self, candidates: List[dict]) -> None:
        self.candidates = candidates
        super().__init__(f"{len(candidates)} candidate persons match this phone number")


class MemberAlreadyAttachedError(CompanyPersonsError):
    """Raised when the phone/person resolves to a user already attached to this company."""


class InvalidCandidatePersonError(CompanyPersonsError):
    """Raised when a caller-supplied `person_id` (disambiguation resend) is not
    in the candidate set computed for `(phone_normalized, caller's admin
    companies)` — the exact same query the 409 `MultipleCandidatesError` path
    uses (H1). Covers: the id does not exist, is not a candidate for this
    phone/company scope (including a foreign company's candidate), or already
    has a linked user account.
    """


class PhoneAlreadyInCompanyError(CompanyPersonsError):
    """Raised when `company_id` already has an active `company_persons` row for
    `phone_normalized` belonging to a DIFFERENT person (H2 — the DB's partial
    unique index on `(company_id, phone_normalized)` allows at most one).

    Re-adding the SAME person is not a conflict — see M1 (reactivation).
    """

    def __init__(self, company_id: UUID, existing_person_id: UUID) -> None:
        self.company_id = company_id
        self.existing_person_id = existing_person_id
        super().__init__(f"Phone number already belongs to person {existing_person_id} in company {company_id}")


class SourceCompanyNotAccessibleError(CompanyPersonsError):
    """Raised by ImportMembersUseCase when the caller is not admin of `from_company_id`."""

    def __init__(self, company_id: UUID) -> None:
        self.company_id = company_id
        super().__init__(f"Caller is not admin of source company {company_id}")
