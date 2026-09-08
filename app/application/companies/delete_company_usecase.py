"""DeleteCompanyUseCase — admin hard-deletes a company and all child rows."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING
from uuid import UUID

from app.application.companies._helpers import _assert_admin
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.companies.exceptions import CompanyHasProjectsError, CompanyNotFoundError

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort

_ADMIN_PERMISSION = "*:*"


class DeleteCompanyUseCase:
    """Hard-delete a company (admin only).

    The DB schema cascades deletion to user_company_access and
    company_invite_tokens via ON DELETE CASCADE.

    Billing documents with company_id FK use ON DELETE SET NULL so
    historical issuer snapshot data is never lost (the issuer_* columns
    on billing_documents remain intact regardless).

    `projects.company_id` is ON DELETE RESTRICT (migration 2ca24be9e3a8): a
    company that still owns projects must not be deleted. When `authz_reader`
    is injected, this precondition is checked explicitly so the caller gets a
    409 with the project count instead of an opaque IntegrityError. Optional
    so existing callers/tests that construct this use case without it keep
    working unchanged (the DB-level RESTRICT still protects correctness).
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
        authz_reader: "Optional[AuthzReaderPort]" = None,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker
        self._authz_reader = authz_reader

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

        # 2b. Refuse to delete a company that still owns projects (FK RESTRICT).
        if self._authz_reader is not None:
            project_ids = self._authz_reader.project_ids_for_company(company_id)
            if project_ids:
                raise CompanyHasProjectsError(company_id, len(project_ids))

        # 3. Delete and commit
        self._company_repo.delete(company_id)
        db_session.commit()
