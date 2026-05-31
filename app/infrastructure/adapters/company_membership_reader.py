"""CompanyMembershipReader — adapts user_company_access table to ICompanyMembershipReader.

Wraps SqlAlchemyUserCompanyAccessRepository.find() so the bibliotheque use-cases
never import infrastructure directly (port/adapter separation).
"""

from __future__ import annotations

from uuid import UUID

from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
    SqlAlchemyUserCompanyAccessRepository,
)


class CompanyMembershipReader:
    """Implements ICompanyMembershipReader via user_company_access lookup."""

    def __init__(self, access_repo: SqlAlchemyUserCompanyAccessRepository) -> None:
        self._access_repo = access_repo

    def is_member(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True if the user has an access row for this company."""
        return self._access_repo.find(user_id, company_id) is not None
