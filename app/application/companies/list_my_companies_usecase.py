"""ListMyCompaniesUseCase — list companies attached to the calling user."""

from __future__ import annotations

from uuid import UUID

from app.application.companies.dtos import (
    CompanyResponse,
    ListMyCompaniesResult,
    MyCompanyResponse,
    UserCompanyAccessResponse,
)
from app.application.companies.ports import CompanyRepositoryPort, RoleCheckerPort
from app.domain.companies.masking import mask_company


class ListMyCompaniesUseCase:
    """Return all companies the caller is attached to.

    Platform ops sees unmasked sensitive fields; everyone else masked values.
    The join with user_company_access is performed by the repository.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker

    def execute(self, caller_id: UUID) -> ListMyCompaniesResult:
        # Admins also see full data in their own list view
        is_admin = self._role_checker.is_platform_admin(caller_id)

        rows = self._company_repo.list_attached_for_user(caller_id)
        items = [
            MyCompanyResponse(
                company=CompanyResponse.from_entity(mask_company(company, full=is_admin)),
                access=UserCompanyAccessResponse.from_entity(access),
            )
            for company, access in rows
        ]
        return ListMyCompaniesResult(items=items)
