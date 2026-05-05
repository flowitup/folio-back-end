"""GetCompanyProfileUseCase — fetch the company profile for a user."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.billing.dtos import CompanyProfileResponse
from app.application.billing.ports import CompanyProfileRepositoryPort


class GetCompanyProfileUseCase:
    """Return the company profile for the given user, or None if not configured."""

    def __init__(self, profile_repo: CompanyProfileRepositoryPort) -> None:
        self._profile_repo = profile_repo

    def execute(self, user_id: UUID) -> Optional[CompanyProfileResponse]:
        profile = self._profile_repo.find_by_user_id(user_id)
        if profile is None:
            return None
        return CompanyProfileResponse.from_entity(profile)
