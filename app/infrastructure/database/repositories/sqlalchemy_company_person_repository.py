"""SQLAlchemy implementation of CompanyPersonRepositoryPort."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.domain.entities.company_person import CompanyPerson
from app.infrastructure.database.models.company_person import CompanyPersonModel


def _ensure_utc(dt: "Optional[datetime]") -> "Optional[datetime]":
    """Attach UTC to a naive datetime — SQLite returns naive values for
    ``DateTime(timezone=True)`` columns, Postgres returns aware ones (same
    normalisation as ``company_serializers._ensure_utc``, duplicated here to
    avoid a companies -> company_persons module dependency)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class SqlAlchemyCompanyPersonRepository(CompanyPersonRepositoryPort):
    """Persistence adapter for CompanyPerson."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find(self, company_id: UUID, person_id: UUID) -> Optional[CompanyPerson]:
        # .first() rather than scalar_one_or_none(): on SQLite the mixed
        # insert paths this codebase uses for UUID columns elsewhere make
        # "more than one row could theoretically match" a possibility this
        # adapter should degrade gracefully from (return a row) rather than
        # raise MultipleResultsFound — the (company_id, person_id) pair is
        # unique by DB constraint on Postgres regardless.
        stmt = select(CompanyPersonModel).where(
            CompanyPersonModel.company_id == company_id,
            CompanyPersonModel.person_id == person_id,
        )
        row = self._session.execute(stmt).scalars().first()
        return self._to_entity(row) if row is not None else None

    def find_by_phone(self, company_id: UUID, phone_normalized: str) -> Optional[CompanyPerson]:
        stmt = select(CompanyPersonModel).where(
            CompanyPersonModel.company_id == company_id,
            CompanyPersonModel.phone_normalized == phone_normalized,
        )
        row = self._session.execute(stmt).scalars().first()
        return self._to_entity(row) if row is not None else None

    def list_for_company(self, company_id: UUID, include_inactive: bool = False) -> List[CompanyPerson]:
        stmt = select(CompanyPersonModel).where(CompanyPersonModel.company_id == company_id)
        if not include_inactive:
            stmt = stmt.where(CompanyPersonModel.is_active.is_(True))
        stmt = stmt.order_by(CompanyPersonModel.created_at)
        rows = self._session.execute(stmt).scalars().all()
        return [self._to_entity(r) for r in rows]

    def list_for_person(self, person_id: UUID) -> List[CompanyPerson]:
        stmt = (
            select(CompanyPersonModel)
            .where(CompanyPersonModel.person_id == person_id)
            .order_by(CompanyPersonModel.created_at)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [self._to_entity(r) for r in rows]

    def list_pending_by_phone(self, phone_normalized: str, now: datetime) -> List[CompanyPerson]:
        stmt = select(CompanyPersonModel).where(
            CompanyPersonModel.phone_normalized == phone_normalized,
            CompanyPersonModel.pending_expires_at.is_not(None),
            CompanyPersonModel.pending_expires_at > now,
        )
        rows = self._session.execute(stmt).scalars().all()
        return [self._to_entity(r) for r in rows]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, profile: CompanyPerson) -> CompanyPerson:
        row = self._session.get(CompanyPersonModel, profile.id)
        if row is None:
            row = CompanyPersonModel(id=profile.id)
            self._session.add(row)
        self._write_fields(profile, row)
        self._session.flush()
        return self._to_entity(row)

    def deactivate(self, company_id: UUID, person_id: UUID) -> bool:
        stmt = select(CompanyPersonModel).where(
            CompanyPersonModel.company_id == company_id,
            CompanyPersonModel.person_id == person_id,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        row.is_active = False
        self._session.flush()
        return True

    # ------------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _write_fields(profile: CompanyPerson, row: CompanyPersonModel) -> None:
        row.company_id = profile.company_id
        row.person_id = profile.person_id
        row.labor_role_id = profile.labor_role_id
        row.default_daily_rate = profile.default_daily_rate
        row.is_active = profile.is_active
        row.phone_normalized = profile.phone_normalized
        row.pending_expires_at = profile.pending_expires_at
        row.created_by_user_id = profile.created_by_user_id
        row.created_at = profile.created_at

    @staticmethod
    def _to_entity(row: CompanyPersonModel) -> CompanyPerson:
        return CompanyPerson(
            id=row.id,
            company_id=row.company_id,
            person_id=row.person_id,
            created_at=_ensure_utc(row.created_at),
            labor_role_id=row.labor_role_id,
            default_daily_rate=row.default_daily_rate,
            is_active=row.is_active,
            phone_normalized=row.phone_normalized,
            pending_expires_at=_ensure_utc(row.pending_expires_at),
            created_by_user_id=row.created_by_user_id,
        )
