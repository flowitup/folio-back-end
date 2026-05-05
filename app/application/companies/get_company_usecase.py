"""GetCompanyUseCase — retrieve a single company with role-aware masking."""

from __future__ import annotations

from app.application.companies.dtos import CompanyResponse, GetCompanyInput
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import CompanyNotFoundError
from app.domain.companies.masking import mask_company

_ADMIN_PERMISSION = "*:*"


class GetCompanyUseCase:
    """Return a single company, masking sensitive fields for non-admins.

    Access rules:
      - Admin (*:*): always sees the full entity.
      - Non-admin: must have a user_company_access row for this company;
        otherwise 404 (not 403) to avoid company enumeration.
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

    def execute(self, inp: GetCompanyInput) -> CompanyResponse:
        # 1. Resolve admin status (pre-resolved by caller is acceptable;
        #    we re-verify here for defence-in-depth)
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)

        # 2. Load company
        company = self._company_repo.find_by_id(inp.company_id)
        if company is None:
            raise CompanyNotFoundError(inp.company_id)

        # 3. Non-admin must have an access row (return 404 to avoid enumeration)
        if not is_admin:
            access = self._access_repo.find(inp.caller_id, inp.company_id)
            if access is None:
                raise CompanyNotFoundError(inp.company_id)

        # 4. Apply masking and return
        safe_company = mask_company(company, full=is_admin)
        return CompanyResponse.from_entity(safe_company)
