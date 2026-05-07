"""SetPrimaryCompanyUseCase — user designates a company as their primary."""

from __future__ import annotations

from app.application.companies.dtos import SetPrimaryCompanyInput, UserCompanyAccessResponse
from app.application.companies.ports import (
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import UserCompanyAccessNotFoundError


class SetPrimaryCompanyUseCase:
    """Atomically set a company as the calling user's primary.

    Transaction sequence (race-safe):
      1. clear_primary_for_user(user_id)  — sets is_primary=False on all rows.
      2. find_for_update(user_id, company_id) — locks the target row.
      3. save(access.with_updates(is_primary=True)).
      4. commit.

    The DB partial unique index (user_id WHERE is_primary=TRUE) is the
    safety net against concurrent racing updates outside this transaction.

    Raises:
        UserCompanyAccessNotFoundError: The user is not attached to company_id.
    """

    def __init__(
        self,
        access_repo: UserCompanyAccessRepositoryPort,
    ) -> None:
        self._access_repo = access_repo

    def execute(
        self,
        inp: SetPrimaryCompanyInput,
        db_session: TransactionalSessionPort,
    ) -> UserCompanyAccessResponse:
        # 1. Clear all primaries for user (single UPDATE ... WHERE user_id=?)
        self._access_repo.clear_primary_for_user(inp.user_id)

        # 2. Lock target row
        access = self._access_repo.find_for_update(inp.user_id, inp.company_id)
        if access is None:
            raise UserCompanyAccessNotFoundError(inp.user_id, inp.company_id)

        # 3. Set primary
        updated = access.with_updates(is_primary=True)
        saved = self._access_repo.save(updated)
        db_session.commit()
        return UserCompanyAccessResponse.from_entity(saved)
