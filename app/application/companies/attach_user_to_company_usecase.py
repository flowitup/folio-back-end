"""AttachUserToCompanyUseCase — admin attaches an existing user account to a company."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.dtos import AttachUserToCompanyInput, UserCompanyAccessResponse
from app.application.companies.ports import (
    ClockPort,
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import CompanyNotFoundError, TargetUserNotFoundError
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess


class AttachUserToCompanyUseCase:
    """Company or platform admin attaches an existing user account to a company.

    Mirrors the attach step of ``JoinCompanyByCodeUseCase``, but admin-initiated
    from the company side instead of self-service via a join code: the target
    does not need a code, and no push notification fires — no
    `company_member_added` kind exists, and admin bulk paths (import,
    add-by-phone) deliberately stay silent too.

    Idempotent: attaching an already-attached user is a no-op success — the
    existing row (and its role) is returned untouched, never duplicated or
    modified.

    Raises:
        ForbiddenCompanyError: caller is neither a platform admin nor an
            admin of this company (D1).
        CompanyNotFoundError: company_id does not exist.
        TargetUserNotFoundError: target_user_id has no corresponding user account.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        role_checker: RoleCheckerPort,
        clock: ClockPort,
        user_repo: Any,  # UserRepositoryPort — required existence check for target_user_id
        person_repo: Optional[Any] = None,
        company_person_repo: Optional[Any] = None,
    ) -> None:
        self._company_repo = company_repo
        self._access_repo = access_repo
        self._role_checker = role_checker
        self._clock = clock
        self._user_repo = user_repo
        # Optional directory collaborators (see ensure_company_person): a caller
        # wired without the Persons bounded context simply skips the profile
        # instead of failing the attachment it was actually asked to perform.
        self._person_repo = person_repo
        self._company_person_repo = company_person_repo

    def execute(
        self,
        inp: AttachUserToCompanyInput,
        db_session: TransactionalSessionPort,
    ) -> UserCompanyAccessResponse:
        # 1. Company-admin guard (platform '*:*' OR admin of this company) — D1.
        _assert_company_admin(self._role_checker, inp.caller_id, inp.company_id)

        # 2. Assert company exists.
        company = self._company_repo.find_by_id(inp.company_id)
        if company is None:
            raise CompanyNotFoundError(inp.company_id)

        # 3. Assert the target user account exists.
        target_user = self._user_repo.find_by_id(inp.target_user_id)
        if target_user is None:
            raise TargetUserNotFoundError(inp.target_user_id)

        # 4. Idempotent: already attached — leave the row (and its role) untouched.
        existing = self._access_repo.find(inp.target_user_id, inp.company_id)
        if existing is not None:
            return UserCompanyAccessResponse.from_entity(existing)

        # 5. Attach as member; primary only when this is the user's first company.
        now = self._clock.now()
        access = UserCompanyAccess(
            user_id=inp.target_user_id,
            company_id=inp.company_id,
            is_primary=len(self._access_repo.list_for_user(inp.target_user_id)) == 0,
            attached_at=now,
            role=CompanyRole.MEMBER.value,
        )
        self._access_repo.save(access)

        # 6. Keep the "attached ⇒ listed in the directory" invariant every other
        # attach path (join code, invitation accept, boot-cleanup auto-promote)
        # already upholds — otherwise the person disappears from the mobile
        # add-worker picker.
        self._ensure_company_person(inp.target_user_id, inp.company_id, now)

        db_session.commit()
        return UserCompanyAccessResponse.from_entity(access)

    def _ensure_company_person(self, user_id: UUID, company_id: UUID, now: datetime) -> None:
        from app.application.company_persons.ensure_company_person import ensure_company_person

        ensure_company_person(
            persons=self._person_repo,
            company_persons=self._company_person_repo,
            users=self._user_repo,
            user_id=user_id,
            company_id=company_id,
            now=now,
        )
