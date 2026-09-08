"""Company join code: one short code per company, shared out of band, that attaches whoever
types it in the app as a ``member``. Unlike invite tokens it is reusable until revoked.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.dtos import CompanyResponse
from app.application.companies.ports import (
    ClockPort,
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import CompanyAlreadyAttachedError, CompanyNotFoundError
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess

# No 0/O/1/I so the code survives being read aloud or hand-written on site.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


class JoinCodeNotFoundError(Exception):
    """No company has this join code (never issued, mistyped, or revoked)."""


def normalize_join_code(raw: str) -> str:
    """Upper-case and strip separators: ``"k7q2-m9xr"`` → ``"K7Q2M9XR"``."""
    return "".join(ch for ch in raw.strip().upper() if ch.isalnum())


def _allocate_unique_code(company_repo: CompanyRepositoryPort) -> str:
    """Generate a join code not already in use. Raises RuntimeError on exhaustion (32^8 space)."""
    for _ in range(10):
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
        if company_repo.find_by_join_code(candidate) is None:
            return candidate
    raise RuntimeError("Could not allocate a unique join code")  # pragma: no cover - astronomically unlikely


def rotate_join_code_unchecked(
    company_repo: CompanyRepositoryPort,
    clock: ClockPort,
    company_id: UUID,
) -> Optional[str]:
    """Issue a fresh join code for `company_id` WITHOUT an admin-permission check.

    Used internally by boot cleanup ONLY (Phase 2 onboarding, finding 11 /
    H4): the caller there has already been authorized for the boot operation
    itself (an admin booting someone) — that is a different authorization
    question than "can this caller manage this company's join code", which
    `SetJoinCodeUseCase` guards for the direct API route. Self-detach never
    calls this (H4: a member leaving on their own must not invalidate the
    code for everyone else).

    No-op (returns None, does not allocate a new code) when the company does
    not exist OR when it currently has no join code at all — rotation only
    replaces an EXISTING code so a company that never issued one, or had it
    revoked, is not silently handed a fresh one by an unrelated boot. Does
    not commit — the caller's transaction owns that.
    """
    company = company_repo.find_by_id(company_id)
    if company is None or company.join_code is None:
        return None
    code = _allocate_unique_code(company_repo)
    company_repo.save(company.with_updates(join_code=code, updated_at=clock.now()))
    return code


class SetJoinCodeUseCase:
    """Issue or revoke a company's join code (company or platform admin).

    ``role_checker`` and ``caller_id`` are REQUIRED — this use-case-level
    guard is the same defence-in-depth every other company-management
    use-case applies (``_assert_company_admin``). It used to be optional so
    call sites relying solely on the route decorator for authorization kept
    working, but that let a caller silently skip the check by omitting
    ``caller_id`` — every construction/call site now passes both.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        clock: ClockPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._companies = company_repo
        self._clock = clock
        self._role_checker = role_checker

    def execute(
        self,
        company_id: UUID,
        enable: bool,
        db_session: TransactionalSessionPort,
        *,
        caller_id: UUID,
    ) -> Optional[str]:
        company = self._companies.find_by_id(company_id)
        if company is None:
            raise CompanyNotFoundError(company_id)

        _assert_company_admin(self._role_checker, caller_id, company_id)
        code: Optional[str] = _allocate_unique_code(self._companies) if enable else None
        self._companies.save(company.with_updates(join_code=code, updated_at=self._clock.now()))
        db_session.commit()
        return code


class JoinCompanyByCodeUseCase:
    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        clock: ClockPort,
        person_repo: Optional[Any] = None,
        company_person_repo: Optional[Any] = None,
        user_repo: Optional[Any] = None,
    ) -> None:
        self._companies = company_repo
        self._access = access_repo
        self._clock = clock
        # Phase 2 onboarding: creates an active, linked company_persons row
        # for the joiner so they show up in the company directory right away.
        # Optional so existing wiring/tests without the Persons BC keep working.
        self._persons = person_repo
        self._company_persons = company_person_repo
        self._users = user_repo

    def execute(self, user_id: UUID, raw_code: str, db_session: TransactionalSessionPort) -> CompanyResponse:
        code = normalize_join_code(raw_code)
        company = self._companies.find_by_join_code(code) if code else None
        if company is None:
            raise JoinCodeNotFoundError()
        if self._access.find(user_id, company.id) is not None:
            raise CompanyAlreadyAttachedError(user_id, company.id)
        now: datetime = self._clock.now()
        self._access.save(
            UserCompanyAccess(
                user_id=user_id,
                company_id=company.id,
                is_primary=len(self._access.list_for_user(user_id)) == 0,
                attached_at=now,
                role=CompanyRole.MEMBER.value,
            )
        )
        self._ensure_company_person(user_id, company.id, now)
        db_session.commit()
        return CompanyResponse.from_entity(company)

    def _ensure_company_person(self, user_id: UUID, company_id: UUID, now: datetime) -> None:
        """Keep the "attached ⇒ listed in the directory" invariant (shared helper)."""
        from app.application.company_persons.ensure_company_person import ensure_company_person

        ensure_company_person(
            persons=self._persons,
            company_persons=self._company_persons,
            users=self._users,
            user_id=user_id,
            company_id=company_id,
            now=now,
        )
