"""SQLAlchemy adapter for `MemberGrantRepositoryPort` (D8 `company_member_grants`).

Backs `app.application.company_persons.manage_grants_usecase.ManageGrantsUseCase`.
Read access for the resolver itself goes through
`app.infrastructure.database.repositories.sqlalchemy_authz_reader
.SqlAlchemyAuthzReader.grants_for` — this adapter is the WRITE (+ per-member
list) side used only by the admin-facing grants management endpoints.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.company_persons.grants_ports import MemberGrant
from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel


class SqlAlchemyCompanyMemberGrantRepository:
    """Implements `MemberGrantRepositoryPort` against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_member(self, company_id: UUID, user_id: UUID) -> List[MemberGrant]:
        rows = (
            self._session.query(CompanyMemberGrantModel)
            .filter(
                CompanyMemberGrantModel.company_id == company_id,
                CompanyMemberGrantModel.user_id == user_id,
            )
            .order_by(CompanyMemberGrantModel.granted_at.asc())
            .all()
        )
        return [self._to_dto(row) for row in rows]

    def upsert(self, grant: MemberGrant) -> MemberGrant:
        row = self._find_row(grant.company_id, grant.user_id, grant.permission, grant.project_id)
        if row is None:
            row = CompanyMemberGrantModel(
                id=grant.id,
                company_id=grant.company_id,
                user_id=grant.user_id,
                permission=grant.permission,
                effect=grant.effect,
                project_id=grant.project_id,
                granted_by_user_id=grant.granted_by_user_id,
                granted_at=grant.granted_at,
            )
            self._session.add(row)
            try:
                self._session.flush()
            except IntegrityError:
                # Concurrent PUT for the same (company, user, permission,
                # project) key raced this insert — re-read the row the other
                # request just committed and update it in place instead of
                # surfacing a raw DB error to the caller.
                self._session.rollback()
                row = self._find_row(grant.company_id, grant.user_id, grant.permission, grant.project_id)
                if row is None:
                    raise
                row.effect = grant.effect
                row.granted_by_user_id = grant.granted_by_user_id
                row.granted_at = grant.granted_at
                self._session.flush()
        else:
            # Replace in place (idempotent upsert): the row's identity is the
            # (company, user, permission, project) key, not its own `id` —
            # changing the effect on the same key never creates a second row.
            row.effect = grant.effect
            row.granted_by_user_id = grant.granted_by_user_id
            row.granted_at = grant.granted_at
            self._session.flush()
        return self._to_dto(row)

    def delete(self, company_id: UUID, user_id: UUID, permission: str, project_id: Optional[UUID]) -> bool:
        row = self._find_row(company_id, user_id, permission, project_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def _find_row(
        self, company_id: UUID, user_id: UUID, permission: str, project_id: Optional[UUID]
    ) -> Optional[CompanyMemberGrantModel]:
        query = self._session.query(CompanyMemberGrantModel).filter(
            CompanyMemberGrantModel.company_id == company_id,
            CompanyMemberGrantModel.user_id == user_id,
            CompanyMemberGrantModel.permission == permission,
        )
        if project_id is None:
            query = query.filter(CompanyMemberGrantModel.project_id.is_(None))
        else:
            query = query.filter(CompanyMemberGrantModel.project_id == project_id)
        return query.one_or_none()

    @staticmethod
    def _to_dto(row: CompanyMemberGrantModel) -> MemberGrant:
        return MemberGrant(
            id=row.id,
            company_id=row.company_id,
            user_id=row.user_id,
            permission=row.permission,
            effect=row.effect,
            project_id=row.project_id,
            granted_by_user_id=row.granted_by_user_id,
            granted_at=row.granted_at,
        )
