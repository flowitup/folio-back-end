"""SQLAlchemy adapter implementing BillingTemplateRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.template import BillingDocumentTemplate
from app.infrastructure.database.models.billing_document_template import (
    BillingDocumentTemplateModel,
)
from app.infrastructure.database.serializers.billing_serializers import (
    deserialize_orm_to_template,
    serialize_template_to_orm,
)


class SqlAlchemyBillingTemplateRepository:
    """Implements BillingTemplateRepositoryPort against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find_by_id(self, template_id: UUID) -> Optional[BillingDocumentTemplate]:
        """Return template by UUID, or None if not found."""
        row = self._session.get(BillingDocumentTemplateModel, template_id)
        if row is None:
            return None
        return deserialize_orm_to_template(row)

    def list_for_user(
        self,
        user_id: UUID,
        kind: Optional[BillingDocumentKind] = None,
    ) -> list[BillingDocumentTemplate]:
        """Return all templates for a user, optionally filtered by kind."""
        stmt = select(BillingDocumentTemplateModel).where(BillingDocumentTemplateModel.user_id == user_id)
        if kind is not None:
            stmt = stmt.where(BillingDocumentTemplateModel.kind == kind.value)
        stmt = stmt.order_by(BillingDocumentTemplateModel.name)
        rows = self._session.execute(stmt).scalars().all()
        return [deserialize_orm_to_template(r) for r in rows]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, template: BillingDocumentTemplate) -> BillingDocumentTemplate:
        """Insert or update a template. Returns the persisted instance."""
        row = self._session.get(BillingDocumentTemplateModel, template.id)
        if row is None:
            row = BillingDocumentTemplateModel()
            serialize_template_to_orm(template, row)
            self._session.add(row)
        else:
            serialize_template_to_orm(template, row)
        self._session.flush()
        return deserialize_orm_to_template(row)

    def delete(self, template_id: UUID) -> None:
        """Hard-delete a template by UUID. No-op if not found."""
        row = self._session.get(BillingDocumentTemplateModel, template_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()
