"""DeleteCompanyUseCase — admin hard-deletes a company and all child rows."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING
from uuid import UUID

from app.application.companies._helpers import _assert_admin
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import CompanyHasProjectsError, CompanyNotFoundError

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort


class DeleteCompanyUseCase:
    """Hard-delete a company (platform ops only).

    The DB schema cascades deletion to user_company_access via ON DELETE CASCADE.

    Billing documents with company_id FK use ON DELETE SET NULL so
    historical issuer snapshot data is never lost (the issuer_* columns
    on billing_documents remain intact regardless).

    `projects.company_id` is ON DELETE RESTRICT (migration 2ca24be9e3a8): a
    company that still owns projects must not be deleted. When `authz_reader`
    is injected, this precondition is checked explicitly so the caller gets a
    409 with the project count instead of an opaque IntegrityError. Optional
    so existing callers/tests that construct this use case without it keep
    working unchanged (the DB-level RESTRICT still protects correctness).

    When `access_repo` is injected, every member whose PRIMARY company this was
    gets their earliest-attached remaining company promoted to primary, like
    boot and detach do; otherwise company-defaulting routes (library,
    inventory, labor roles) would find no company for them.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        role_checker: RoleCheckerPort,
        authz_reader: "Optional[AuthzReaderPort]" = None,
        access_repo: Optional[UserCompanyAccessRepositoryPort] = None,
    ) -> None:
        self._company_repo = company_repo
        self._role_checker = role_checker
        self._authz_reader = authz_reader
        self._access_repo = access_repo

    def execute(
        self,
        caller_id: UUID,
        company_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        # 1. Admin guard
        is_admin = self._role_checker.is_platform_admin(caller_id)
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

        # 3. Delete, re-promote a primary for whoever had this one, and commit
        access_repo = self._access_repo
        primary_user_ids: list[UUID] = []
        if access_repo is not None:
            primary_user_ids = [a.user_id for a in access_repo.list_for_company(company_id) if a.is_primary]
        self._company_repo.delete(company_id)
        if access_repo is not None:
            for user_id in primary_user_ids:
                # Filter the deleted company out: its rows go by ON DELETE CASCADE,
                # which the session may not reflect yet.
                remaining = [r for r in access_repo.list_for_user(user_id) if r.company_id != company_id]
                if remaining and not any(r.is_primary for r in remaining):
                    first = min(remaining, key=lambda r: r.attached_at)
                    access_repo.save(first.with_updates(is_primary=True))
        db_session.commit()
