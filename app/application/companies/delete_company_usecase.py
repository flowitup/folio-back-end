"""DeleteCompanyUseCase — admin hard-deletes a company and all child rows."""

from __future__ import annotations

from uuid import UUID

from app.application.companies._helpers import _assert_admin
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.companies.exceptions import CompanyNotFoundError

_ADMIN_PERMISSION = "*:*"


class DeleteCompanyUseCase:
    """Hard-delete a company (admin only).

    The DB schema cascades deletion to user_company_access and
    company_invite_tokens via ON DELETE CASCADE.

    Billing documents with company_id FK use ON DELETE SET NULL so
    historical issuer snapshot data is never lost (the issuer_* columns
    on billing_documents remain intact regardless).
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker

    def execute(
        self,
        caller_id: UUID,
        company_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(caller_id, _ADMIN_PERMISSION)
        _assert_admin(caller_id, company_id, is_admin)

        # 2. Assert company exists before deleting
        company = self._company_repo.find_by_id(company_id)
        if company is None:
            raise CompanyNotFoundError(company_id)

        # 3. Delete and commit
        self._company_repo.delete(company_id)
        db_session.commit()
