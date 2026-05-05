"""SQLAlchemy adapter implementing CompanyProfileRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.billing.company_profile import CompanyProfile
from app.infrastructure.database.models.company_profile import CompanyProfileModel
from app.infrastructure.database.serializers.billing_serializers import (
    deserialize_orm_to_profile,
    serialize_profile_to_orm,
)


class SqlAlchemyCompanyProfileRepository:
    """Implements CompanyProfileRepositoryPort against a SQLAlchemy session.

    company_profile uses user_id as both PK and unique key, so save() is
    effectively an upsert: update if row exists, insert otherwise.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_user_id(self, user_id: UUID) -> Optional[CompanyProfile]:
        """Return the company profile for a user, or None if not configured."""
        row = self._session.get(CompanyProfileModel, user_id)
        if row is None:
            return None
        return deserialize_orm_to_profile(row)

    def save(self, profile: CompanyProfile) -> CompanyProfile:
        """Upsert a company profile by user_id. Returns the persisted instance."""
        row = self._session.get(CompanyProfileModel, profile.user_id)
        if row is None:
            row = CompanyProfileModel()
            serialize_profile_to_orm(profile, row)
            self._session.add(row)
        else:
            serialize_profile_to_orm(profile, row)
        self._session.flush()
        return deserialize_orm_to_profile(row)
