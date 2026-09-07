"""SetMemberRoleUseCase — admin promotes/demotes a company member's role."""

from __future__ import annotations

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.dtos import SetMemberRoleInput, UserCompanyAccessResponse
from app.application.companies.ports import (
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import (
    LastCompanyAdminError,
    UserCompanyAccessNotFoundError,
)
from app.domain.companies.roles import CompanyRole


class SetMemberRoleUseCase:
    """Change a company member's per-company role. Company or platform admin only.

    Guards:
      - Caller must be a platform admin ('*:*') or an admin of this company.
      - Target user must be attached to the company.
      - Changing the company's last admin to any non-admin role is rejected
        (LastCompanyAdminError) so every company keeps at least one admin who
        can manage its billing and members.
    """

    def __init__(
        self,
        access_repo: UserCompanyAccessRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._access_repo = access_repo
        self._role_checker = role_checker

    def execute(
        self,
        inp: SetMemberRoleInput,
        db_session: TransactionalSessionPort,
    ) -> UserCompanyAccessResponse:
        # 1. Company-admin guard (platform '*:*' OR admin of this company)
        _assert_company_admin(self._role_checker, inp.caller_id, inp.company_id)

        # 2. Validate role
        if inp.role not in CompanyRole.values():
            raise ValueError(f"Invalid company role: {inp.role!r} (expected one of {CompanyRole.values()})")

        # 3. Lock target access row
        access = self._access_repo.find_for_update(inp.user_id, inp.company_id)
        if access is None:
            raise UserCompanyAccessNotFoundError(inp.user_id, inp.company_id)

        # 4. No-op fast path
        if access.role == inp.role:
            return UserCompanyAccessResponse.from_entity(access)

        # 5. Last-admin guard: block any change away from admin that would leave
        # the company with zero admins. Locks the admin rows (FOR UPDATE) so a
        # concurrent demote/boot/self-detach cannot race past this count.
        if access.role == CompanyRole.ADMIN.value and inp.role != CompanyRole.ADMIN.value:
            admins = self._access_repo.list_admins_for_update(inp.company_id)
            if len(admins) <= 1:
                raise LastCompanyAdminError(inp.company_id)

        # 6. Apply + persist
        updated = access.with_updates(role=inp.role)
        saved = self._access_repo.save(updated)
        db_session.commit()
        return UserCompanyAccessResponse.from_entity(saved)
