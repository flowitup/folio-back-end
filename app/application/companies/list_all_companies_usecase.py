"""ListAllCompaniesUseCase — admin lists all companies (paginated)."""

from __future__ import annotations

from app.application.companies._helpers import _assert_admin
from app.application.companies.dtos import (
    CompanyResponse,
    ListAllCompaniesInput,
    ListAllCompaniesResult,
)
from app.application.companies.ports import CompanyRepositoryPort, RoleCheckerPort

_ADMIN_PERMISSION = "*:*"
_MAX_LIMIT = 200


class ListAllCompaniesUseCase:
    """Return a paginated list of all companies (admin only).

    Non-admin callers should use ListMyCompaniesUseCase instead.
    Sensitive fields are returned unmasked to admins.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker

    def execute(self, inp: ListAllCompaniesInput) -> ListAllCompaniesResult:
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)
        # Use caller_id twice — no specific company being accessed
        _assert_admin(inp.caller_id, inp.caller_id, is_admin)

        # 2. Clamp limit
        limit = min(max(1, inp.limit), _MAX_LIMIT)
        offset = max(0, inp.offset)

        # 3. Fetch and return (no masking for admins)
        companies, total = self._company_repo.list_all(limit=limit, offset=offset)
        return ListAllCompaniesResult(
            items=[CompanyResponse.from_entity(c) for c in companies],
            total=total,
        )
