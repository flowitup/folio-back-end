"""ListAttachedUsersUseCase — admin lists all users attached to a company."""

from __future__ import annotations

from app.application.companies._helpers import _assert_admin
from app.application.companies.dtos import (
    ListAttachedUsersInput,
    UserCompanyAccessResponse,
)
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import CompanyNotFoundError

_ADMIN_PERMISSION = "*:*"


class ListAttachedUsersUseCase:
    """Return all user_company_access rows for a given company (admin only).

    Returns UserCompanyAccessResponse list; the API layer may enrich with
    user display names by joining against the users table (phase 04).
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

    def execute(self, inp: ListAttachedUsersInput) -> list[UserCompanyAccessResponse]:
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)
        _assert_admin(inp.caller_id, inp.company_id, is_admin)

        # 2. Assert company exists
        company = self._company_repo.find_by_id(inp.company_id)
        if company is None:
            raise CompanyNotFoundError(inp.company_id)

        # 3. Return access rows
        accesses = self._access_repo.list_for_company(inp.company_id)
        return [UserCompanyAccessResponse.from_entity(a) for a in accesses]
