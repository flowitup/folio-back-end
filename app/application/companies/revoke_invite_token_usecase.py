"""RevokeInviteTokenUseCase — admin revokes the active invite token for a company."""

from __future__ import annotations

from uuid import UUID

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.ports import (
    CompanyInviteTokenRepositoryPort,
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
)
from app.domain.companies.exceptions import CompanyNotFoundError, InviteTokenNotFoundError


class RevokeInviteTokenUseCase:
    """Delete the active invite token for a company (company or platform admin).

    Raises InviteTokenNotFoundError if no active token exists.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        token_repo: CompanyInviteTokenRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._token_repo = token_repo
        self._role_checker = role_checker

    def execute(
        self,
        caller_id: UUID,
        company_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        # 1. Company-admin guard (platform '*:*' OR admin of this company)
        _assert_company_admin(self._role_checker, caller_id, company_id)

        # 2. Assert company exists
        company = self._company_repo.find_by_id(company_id)
        if company is None:
            raise CompanyNotFoundError(company_id)

        # 3. Find and delete active token
        token = self._token_repo.find_active_for_company(company_id)
        if token is None:
            raise InviteTokenNotFoundError(company_id)

        self._token_repo.delete(token.id)
        db_session.commit()
