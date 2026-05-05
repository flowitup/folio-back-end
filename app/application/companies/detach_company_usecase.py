"""DetachCompanyUseCase — user removes their own access to a company."""

from __future__ import annotations

from app.application.companies.dtos import DetachCompanyInput
from app.application.companies.ports import (
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import UserCompanyAccessNotFoundError


class DetachCompanyUseCase:
    """Remove the calling user's access to a company.

    If the detached company was the user's primary AND the user still has
    other attachments, the first remaining (lowest attached_at) is
    auto-promoted to primary.

    If the user has no remaining attachments after detach, no primary
    is set (user has zero companies — valid transitional state).

    Users can only detach themselves; admins booting a user should use
    BootAttachedUserUseCase instead.
    """

    def __init__(
        self,
        access_repo: UserCompanyAccessRepositoryPort,
    ) -> None:
        self._access_repo = access_repo

    def execute(
        self,
        inp: DetachCompanyInput,
        db_session: TransactionalSessionPort,
    ) -> None:
        # 1. Load the access row to be removed
        access = self._access_repo.find(inp.user_id, inp.company_id)
        if access is None:
            raise UserCompanyAccessNotFoundError(inp.user_id, inp.company_id)

        was_primary = access.is_primary

        # 2. Delete the access row
        self._access_repo.delete(inp.user_id, inp.company_id)

        # 3. If detached company was primary, auto-promote first remaining
        if was_primary:
            remaining = self._access_repo.list_for_user(inp.user_id)
            # Filter out the just-deleted row (repo may or may not reflect it yet)
            remaining = [r for r in remaining if r.company_id != inp.company_id]
            if remaining:
                # Promote the earliest-attached company (deterministic choice)
                first = min(remaining, key=lambda r: r.attached_at)
                promoted = first.with_updates(is_primary=True)
                self._access_repo.save(promoted)

        db_session.commit()
