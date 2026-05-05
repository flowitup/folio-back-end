"""SQLAlchemy adapter implementing CompanyRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.companies.company import Company
from app.domain.companies.user_company_access import UserCompanyAccess
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from app.infrastructure.database.repositories.company_serializers import (
    deserialize_company_orm,
    deserialize_access_orm,
    serialize_company_to_orm,
)


class SqlAlchemyCompanyRepository:
    """Implements CompanyRepositoryPort against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find_by_id(self, company_id: UUID) -> Optional[Company]:
        """Return company by UUID, or None if not found."""
        row = self._session.get(CompanyModel, company_id)
        if row is None:
            return None
        return deserialize_company_orm(row)

    def find_by_id_for_update(self, company_id: UUID) -> Optional[Company]:
        """Return company with SELECT FOR UPDATE lock, or None."""
        stmt = select(CompanyModel).where(CompanyModel.id == company_id).with_for_update()
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_company_orm(row)

    def list_all(self, limit: int, offset: int) -> tuple[list[Company], int]:
        """Return paginated companies with total count (admin view)."""
        base = select(CompanyModel)
        count_stmt = select(func.count()).select_from(base.subquery())
        total: int = self._session.execute(count_stmt).scalar_one()
        rows_stmt = base.order_by(CompanyModel.legal_name).limit(limit).offset(offset)
        rows = self._session.execute(rows_stmt).scalars().all()
        return ([deserialize_company_orm(r) for r in rows], total)

    def list_attached_for_user(self, user_id: UUID) -> list[tuple[Company, UserCompanyAccess]]:
        """Return (Company, UserCompanyAccess) pairs for a user's attached companies."""
        stmt = (
            select(CompanyModel, UserCompanyAccessModel)
            .join(
                UserCompanyAccessModel,
                UserCompanyAccessModel.company_id == CompanyModel.id,
            )
            .where(UserCompanyAccessModel.user_id == user_id)
            .order_by(
                UserCompanyAccessModel.is_primary.desc(),
                UserCompanyAccessModel.attached_at,
            )
        )
        rows = self._session.execute(stmt).all()
        return [
            (deserialize_company_orm(company_row), deserialize_access_orm(access_row))
            for company_row, access_row in rows
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, company: Company) -> Company:
        """Insert or update a company. Returns the persisted instance."""
        row = self._session.get(CompanyModel, company.id)
        if row is None:
            row = CompanyModel()
            serialize_company_to_orm(company, row)
            self._session.add(row)
        else:
            serialize_company_to_orm(company, row)
        self._session.flush()
        return deserialize_company_orm(row)

    def delete(self, company_id: UUID) -> None:
        """Hard-delete a company by UUID. No-op if not found."""
        row = self._session.get(CompanyModel, company_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()
