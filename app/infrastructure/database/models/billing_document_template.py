"""SQLAlchemy ORM model for billing_document_templates table."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base

ItemsJSON = JSON().with_variant(JSONB(), "postgresql")


class BillingDocumentTemplateModel(Base):
    """ORM model for billing_document_templates.

    Templates store reusable structure (items, notes, terms, default VAT rate)
    but never recipient data, dates, status, or document numbers.
    """

    __tablename__ = "billing_document_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind = Column(
        Enum("devis", "facture", name="billing_document_kind", create_type=False),
        nullable=False,
    )
    name = Column(String(120), nullable=False)
    notes = Column(Text, nullable=True)
    terms = Column(Text, nullable=True)
    # NUMERIC(5,2) — stored as string in Python via Decimal for precision
    default_vat_rate = Column(Numeric(precision=5, scale=2), nullable=True)
    items = Column(ItemsJSON, nullable=False, default=list)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("UserModel", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "kind",
            "name",
            name="uq_billing_template_user_kind_name",
        ),
        Index(
            "ix_billing_document_templates_user_kind",
            "user_id",
            "kind",
        ),
    )

    def __repr__(self) -> str:
        return f"<BillingDocumentTemplate {self.name!r} kind={self.kind}>"
