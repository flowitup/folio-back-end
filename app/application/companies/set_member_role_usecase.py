"""SetMemberRoleUseCase — admin promotes/demotes a company member's role."""

from __future__ import annotations

from typing import Any, Optional

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

    Demoting an admin also drops their `user_projects` rows on THIS company's projects, in the
    same transaction: those rows were created for them as an admin (project creator and the
    auto-assignment of every other admin), and left behind they would keep manager-level access
    to every older project. A row carries no role or origin, so a deliberate assignment cannot be
    told apart and goes with them; an admin re-assigns what the member should keep. Rows on other
    companies' projects are untouched. Skipped when the optional collaborators are not injected.
    """

    def __init__(
        self,
        access_repo: UserCompanyAccessRepositoryPort,
        role_checker: RoleCheckerPort,
        authz_reader: Optional[Any] = None,
        membership_repo: Optional[Any] = None,
        task_repo: Optional[Any] = None,
    ) -> None:
        self._access_repo = access_repo
        self._role_checker = role_checker
        self._authz_reader = authz_reader
        self._membership_repo = membership_repo
        self._task_repo = task_repo

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
        demoted_admin = access.role == CompanyRole.ADMIN.value and inp.role != CompanyRole.ADMIN.value
        if demoted_admin:
            admins = self._access_repo.list_admins_for_update(inp.company_id)
            if len(admins) <= 1:
                raise LastCompanyAdminError(inp.company_id)

        # 6. Apply + persist
        updated = access.with_updates(role=inp.role)
        saved = self._access_repo.save(updated)
        if demoted_admin and self._authz_reader is not None and self._membership_repo is not None:
            for project_id in self._authz_reader.project_ids_for_company(inp.company_id):
                self._membership_repo.remove(inp.user_id, project_id)
                # No task of the company keeps naming (and pushing to) someone who lost the project.
                if self._task_repo is not None:
                    self._task_repo.clear_assignee(project_id, inp.user_id)
        db_session.commit()
        return UserCompanyAccessResponse.from_entity(saved)
