"""ListAttachedUsersUseCase — admin lists all users attached to a company."""

from __future__ import annotations

from app.application.companies._helpers import _assert_admin
from app.application.companies.dtos import (
    ListAttachedUsersInput,
    ListAttachedUsersResult,
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
    """Return paginated user_company_access rows for a given company (admin only).

    H5: supports limit/offset pagination; returns ListAttachedUsersResult
    with items and total count.
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

    def execute(self, inp: ListAttachedUsersInput) -> ListAttachedUsersResult:
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)
        _assert_admin(inp.caller_id, inp.company_id, is_admin)

        # 2. Assert company exists
        company = self._company_repo.find_by_id(inp.company_id)
        if company is None:
            raise CompanyNotFoundError(inp.company_id)

        # 3. Return paginated access rows
        all_accesses = self._access_repo.list_for_company(inp.company_id)
        total = len(all_accesses)
        page = all_accesses[inp.offset : inp.offset + inp.limit]
        items = [UserCompanyAccessResponse.from_entity(a) for a in page]
        return ListAttachedUsersResult(items=items, total=total)
