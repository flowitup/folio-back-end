"""SQLAlchemy adapter implementing CompanyInviteTokenRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.companies.invite_token import CompanyInviteToken
from app.infrastructure.database.models.company_invite_token import CompanyInviteTokenModel
from app.infrastructure.database.repositories.company_serializers import (
    deserialize_token_orm,
    serialize_token_to_orm,
)


class SqlAlchemyCompanyInviteTokenRepository:
    """Implements CompanyInviteTokenRepositoryPort against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find_active_for_company(self, company_id: UUID) -> Optional[CompanyInviteToken]:
        """Return the single unredeemed token for a company, or None.

        Does not filter by expiry here — expiry check is the use-case's
        responsibility so callers can surface a meaningful error message.
        """
        stmt = select(CompanyInviteTokenModel).where(
            CompanyInviteTokenModel.company_id == company_id,
            CompanyInviteTokenModel.redeemed_at.is_(None),
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_token_orm(row)

    def find_by_id_for_update(self, token_id: UUID) -> Optional[CompanyInviteToken]:
        """Return the token with SELECT FOR UPDATE lock, or None.

        Used by RedeemInviteTokenUseCase to serialise concurrent redemptions
        on the same token row.
        """
        stmt = select(CompanyInviteTokenModel).where(CompanyInviteTokenModel.id == token_id).with_for_update()
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_token_orm(row)

    def list_active(self) -> list[CompanyInviteToken]:
        """Return all active (unredeemed) tokens.

        Used by RedeemInviteTokenUseCase to verify a plaintext token against
        stored argon2 hashes (bounded to ≤ 1 000 active tokens per DOS guard
        enforced in the use-case).
        """
        stmt = select(CompanyInviteTokenModel).where(
            CompanyInviteTokenModel.redeemed_at.is_(None),
        )
        rows = self._session.execute(stmt).scalars().all()
        return [deserialize_token_orm(r) for r in rows]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, token: CompanyInviteToken) -> CompanyInviteToken:
        """Insert or update a token row. Returns the persisted instance."""
        row = self._session.get(CompanyInviteTokenModel, token.id)
        if row is None:
            row = CompanyInviteTokenModel()
            serialize_token_to_orm(token, row)
            self._session.add(row)
        else:
            serialize_token_to_orm(token, row)
        self._session.flush()
        return deserialize_token_orm(row)

    def delete(self, token_id: UUID) -> None:
        """Hard-delete a token row by UUID. No-op if not found."""
        row = self._session.get(CompanyInviteTokenModel, token_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()
