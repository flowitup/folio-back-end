"""DetachCompanyUseCase — user removes their own access to a company."""

from __future__ import annotations

from typing import Any, Optional

from app.application.companies.dtos import DetachCompanyInput
from app.application.companies.join_code_usecases import rotate_join_code_unchecked
from app.application.companies.ports import (
    ClockPort,
    CompanyRepositoryPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import LastCompanyAdminError, UserCompanyAccessNotFoundError
from app.domain.companies.roles import CompanyRole


class DetachCompanyUseCase:
    """Remove the calling user's access to a company.

    If the detached company was the user's primary AND the user still has
    other attachments, the first remaining (lowest attached_at) is
    auto-promoted to primary.

    If the user has no remaining attachments after detach, no primary
    is set (user has zero companies — valid transitional state).

    Users can only detach themselves; admins booting a user should use
    BootAttachedUserUseCase instead.

    Phase 2 onboarding cleanup, same transaction as the access-row delete:
    the departing user's assignments on this company's projects are removed,
    their `company_persons` profile (if any) is deactivated, and the join
    code is rotated (best-effort — skipped when the optional collaborators
    are not injected, so existing callers/tests keep working).

    Raises:
        UserCompanyAccessNotFoundError: caller is not attached to the company.
        LastCompanyAdminError: caller is the company's last remaining admin —
            self-detach is rejected so the company never ends with zero admins.
    """

    def __init__(
        self,
        access_repo: UserCompanyAccessRepositoryPort,
        authz_reader: Optional[Any] = None,
        membership_repo: Optional[Any] = None,
        person_repo: Optional[Any] = None,
        company_person_repo: Optional[Any] = None,
        company_repo: Optional[CompanyRepositoryPort] = None,
        clock: Optional[ClockPort] = None,
    ) -> None:
        self._access_repo = access_repo
        self._authz_reader = authz_reader
        self._membership_repo = membership_repo
        self._person_repo = person_repo
        self._company_person_repo = company_person_repo
        self._company_repo = company_repo
        self._clock = clock

    def execute(
        self,
        inp: DetachCompanyInput,
        db_session: TransactionalSessionPort,
    ) -> None:
        # 1. Load the access row to be removed
        access = self._access_repo.find(inp.user_id, inp.company_id)
        if access is None:
            raise UserCompanyAccessNotFoundError(inp.user_id, inp.company_id)

        # 1b. Last-admin guard: self-detaching the company's only admin is
        # rejected. Locks the admin rows (FOR UPDATE) so a concurrent
        # demote/boot of another admin cannot race past this count.
        if access.role == CompanyRole.ADMIN.value:
            admins = self._access_repo.list_admins_for_update(inp.company_id)
            if len(admins) <= 1:
                raise LastCompanyAdminError(inp.company_id)

        was_primary = access.is_primary

        # 2. Delete the access row
        self._access_repo.delete(inp.user_id, inp.company_id)

        # 3. If detached company was primary, auto-promote first remaining
        if was_primary:
            remaining = self._access_repo.list_for_user(inp.user_id)
            # Filter out the just-deleted row (repo may or may not reflect it yet)
            remaining = [r for r in remaining if r.company_id != inp.company_id]
            if remaining:
                # Promote the earliest-attached company (deterministic choice)
                first = min(remaining, key=lambda r: r.attached_at)
                promoted = first.with_updates(is_primary=True)
                self._access_repo.save(promoted)

        # 4. Phase 2 onboarding cleanup, same transaction: drop the departing
        # user's assignments on this company's projects, deactivate their
        # directory profile, and rotate the join code.
        if self._authz_reader is not None and self._membership_repo is not None:
            for project_id in self._authz_reader.project_ids_for_company(inp.company_id):
                self._membership_repo.remove(inp.user_id, project_id)

        if self._person_repo is not None and self._company_person_repo is not None:
            person = self._person_repo.find_by_user_id(inp.user_id)
            if person is not None:
                self._company_person_repo.deactivate(inp.company_id, person.id)

        if self._company_repo is not None and self._clock is not None:
            rotate_join_code_unchecked(self._company_repo, self._clock, inp.company_id)

        db_session.commit()
