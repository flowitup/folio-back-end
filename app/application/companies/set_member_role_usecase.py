"""SetMemberRoleUseCase — admin promotes/demotes a company member's role."""

from __future__ import annotations

from app.application.companies._helpers import _assert_admin
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

_ADMIN_PERMISSION = "*:*"


class SetMemberRoleUseCase:
    """Change a company member's per-company role (admin | member). Admin only.

    Guards:
      - Caller must hold the global *:* admin permission.
      - Target user must be attached to the company.
      - Demoting the company's last admin is rejected (LastCompanyAdminError) so
        every company keeps at least one admin who can manage its billing.
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
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)
        _assert_admin(inp.caller_id, inp.company_id, is_admin)

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

        # 5. Last-admin guard: block demoting the only admin of the company
        if access.role == CompanyRole.ADMIN.value and inp.role == CompanyRole.MEMBER.value:
            admins = [a for a in self._access_repo.list_for_company(inp.company_id) if a.role == CompanyRole.ADMIN.value]
            if len(admins) <= 1:
                raise LastCompanyAdminError(inp.company_id)

        # 6. Apply + persist
        updated = access.with_updates(role=inp.role)
        saved = self._access_repo.save(updated)
        db_session.commit()
        return UserCompanyAccessResponse.from_entity(saved)
