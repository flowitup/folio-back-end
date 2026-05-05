"""SQLAlchemy adapter implementing BillingDocumentRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.infrastructure.database.models.billing_document import BillingDocumentModel
from app.infrastructure.database.serializers.billing_serializers import (
    deserialize_orm_to_doc,
    serialize_doc_to_orm,
)


class SqlAlchemyBillingDocumentRepository:
    """Implements BillingDocumentRepositoryPort against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find_by_id(self, doc_id: UUID) -> Optional[BillingDocument]:
        """Return document by UUID, or None if not found."""
        row = self._session.get(BillingDocumentModel, doc_id)
        if row is None:
            return None
        return deserialize_orm_to_doc(row)

    def find_by_id_for_update(self, doc_id: UUID) -> Optional[BillingDocument]:
        """Return document with SELECT FOR UPDATE lock (serialises concurrent ops).

        Falls back to a plain SELECT on dialects that don't support FOR UPDATE
        (e.g. SQLite in tests) — SQLAlchemy silently drops the hint on SQLite.
        """
        stmt = select(BillingDocumentModel).where(BillingDocumentModel.id == doc_id).with_for_update()
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_orm_to_doc(row)

    def list_for_user(
        self,
        user_id: UUID,
        kind: BillingDocumentKind,
        status: Optional[BillingDocumentStatus] = None,
        project_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BillingDocument], int]:
        """Return paginated documents for a user, with unfiltered total count.

        Returns (items, total_count).
        """
        base = select(BillingDocumentModel).where(
            BillingDocumentModel.user_id == user_id,
            BillingDocumentModel.kind == kind.value,
        )
        if status is not None:
            base = base.where(BillingDocumentModel.status == status.value)
        if project_id is not None:
            base = base.where(BillingDocumentModel.project_id == project_id)

        # Total count (no pagination)
        count_stmt = select(func.count()).select_from(base.subquery())
        total: int = self._session.execute(count_stmt).scalar_one()

        # Paginated rows, newest first
        rows_stmt = base.order_by(BillingDocumentModel.created_at.desc()).limit(limit).offset(offset)
        rows = self._session.execute(rows_stmt).scalars().all()
        return ([deserialize_orm_to_doc(r) for r in rows], total)

    def find_by_source_devis_id(self, devis_id: UUID) -> Optional[BillingDocument]:
        """Return the facture linked to a given source devis, or None.

        Used as a race-condition guard in ConvertDevisToFactureUseCase.
        """
        stmt = select(BillingDocumentModel).where(BillingDocumentModel.source_devis_id == devis_id)
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return deserialize_orm_to_doc(row)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, doc: BillingDocument) -> BillingDocument:
        """Insert or update a document. Returns the persisted instance."""
        row = self._session.get(BillingDocumentModel, doc.id)
        if row is None:
            row = BillingDocumentModel()
            serialize_doc_to_orm(doc, row)
            self._session.add(row)
        else:
            serialize_doc_to_orm(doc, row)
        self._session.flush()
        return deserialize_orm_to_doc(row)

    def delete(self, doc_id: UUID) -> None:
        """Hard-delete a document by UUID. No-op if not found."""
        row = self._session.get(BillingDocumentModel, doc_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()
