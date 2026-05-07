"""SQLAlchemy adapter implementing UserCompanyAccessRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.companies.user_company_access import UserCompanyAccess
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from app.infrastructure.database.repositories.company_serializers import (
    deserialize_access_orm,
    serialize_access_to_orm,
)


class SqlAlchemyUserCompanyAccessRepository:
    """Implements UserCompanyAccessRepositoryPort against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find(self, user_id: UUID, company_id: UUID) -> Optional[UserCompanyAccess]:
        """Return the access row for (user_id, company_id), or None."""
        row = self._session.get(UserCompanyAccessModel, (user_id, company_id))
        if row is None:
            return None
        return deserialize_access_orm(row)

    def find_for_update(self, user_id: UUID, company_id: UUID) -> Optional[UserCompanyAccess]:
        """Return the access row with SELECT FOR UPDATE lock, or None."""
        stmt = (
            select(UserCompanyAccessModel)
            .where(
                UserCompanyAccessModel.user_id == user_id,
                UserCompanyAccessModel.company_id == company_id,
            )
            .with_for_update()
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_access_orm(row)

    def list_for_user(self, user_id: UUID) -> list[UserCompanyAccess]:
        """Return all access rows for a user, primary first."""
        stmt = (
            select(UserCompanyAccessModel)
            .where(UserCompanyAccessModel.user_id == user_id)
            .order_by(
                UserCompanyAccessModel.is_primary.desc(),
                UserCompanyAccessModel.attached_at,
            )
        )
        rows = self._session.execute(stmt).scalars().all()
        return [deserialize_access_orm(r) for r in rows]

    def list_for_company(self, company_id: UUID) -> list[UserCompanyAccess]:
        """Return all access rows for a company."""
        stmt = (
            select(UserCompanyAccessModel)
            .where(UserCompanyAccessModel.company_id == company_id)
            .order_by(UserCompanyAccessModel.attached_at)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [deserialize_access_orm(r) for r in rows]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, access: UserCompanyAccess) -> UserCompanyAccess:
        """Insert or update an access row. Returns the persisted instance."""
        row = self._session.get(UserCompanyAccessModel, (access.user_id, access.company_id))
        if row is None:
            row = UserCompanyAccessModel()
            serialize_access_to_orm(access, row)
            self._session.add(row)
        else:
            serialize_access_to_orm(access, row)
        self._session.flush()
        return deserialize_access_orm(row)

    def delete(self, user_id: UUID, company_id: UUID) -> None:
        """Hard-delete the access row for (user_id, company_id). No-op if not found."""
        row = self._session.get(UserCompanyAccessModel, (user_id, company_id))
        if row is not None:
            self._session.delete(row)
            self._session.flush()

    def clear_primary_for_user(self, user_id: UUID) -> None:
        """Set is_primary=False for ALL access rows belonging to user_id.

        Used inside a transaction by SetPrimaryCompanyUseCase to guarantee
        at most one primary per user atomically (clear-all then set-one).
        """
        stmt = update(UserCompanyAccessModel).where(UserCompanyAccessModel.user_id == user_id).values(is_primary=False)
        self._session.execute(stmt)
        self._session.flush()
