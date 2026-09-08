"""BootAttachedUserUseCase — admin removes a specific user's access to a company."""

from __future__ import annotations

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.dtos import BootAttachedUserInput
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import (
    CompanyNotFoundError,
    LastCompanyAdminError,
    UserCompanyAccessNotFoundError,
)
from app.domain.companies.roles import CompanyRole


class BootAttachedUserUseCase:
    """Company or platform admin removes a user's access to a company (boot).

    If the booted user had this company as their primary AND still has
    other attachments, the first remaining attachment is auto-promoted.

    Raises:
        ForbiddenCompanyError: Caller is neither a platform admin nor an
            admin of this company.
        CompanyNotFoundError: company_id does not exist.
        UserCompanyAccessNotFoundError: target_user_id is not attached.
        LastCompanyAdminError: target is the company's last remaining admin.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._access_repo = access_repo
        self._role_checker = role_checker

    def execute(
        self,
        inp: BootAttachedUserInput,
        db_session: TransactionalSessionPort,
    ) -> None:
        # 1. Company-admin guard (platform '*:*' OR admin of this company)
        _assert_company_admin(self._role_checker, inp.caller_id, inp.company_id)

        # 2. Assert company exists
        company = self._company_repo.find_by_id(inp.company_id)
        if company is None:
            raise CompanyNotFoundError(inp.company_id)

        # 3. Load and lock the target access row
        access = self._access_repo.find_for_update(inp.target_user_id, inp.company_id)
        if access is None:
            raise UserCompanyAccessNotFoundError(inp.target_user_id, inp.company_id)

        # 3b. Last-admin guard: booting the company's only admin is rejected.
        # Locks the admin rows (FOR UPDATE) so a concurrent demote/boot/detach
        # of another admin cannot race past this count.
        if access.role == CompanyRole.ADMIN.value:
            admins = self._access_repo.list_admins_for_update(inp.company_id)
            if len(admins) <= 1:
                raise LastCompanyAdminError(inp.company_id)

        was_primary = access.is_primary

        # 4. Delete the row
        self._access_repo.delete(inp.target_user_id, inp.company_id)

        # 5. Auto-promote first remaining if detached company was primary
        if was_primary:
            remaining = self._access_repo.list_for_user(inp.target_user_id)
            remaining = [r for r in remaining if r.company_id != inp.company_id]
            if remaining:
                first = min(remaining, key=lambda r: r.attached_at)
                self._access_repo.save(first.with_updates(is_primary=True))

        db_session.commit()
