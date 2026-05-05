"""SQLAlchemy ORM model for billing_documents table."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base

# JSONB on Postgres, generic JSON elsewhere (SQLite for tests)
ItemsJSON = JSON().with_variant(JSONB(), "postgresql")


class BillingDocumentModel(Base):
    """ORM model for billing_documents.

    Stores both devis and facture rows (kind discriminator).
    items column holds a JSON list of serialized BillingDocumentItem dicts.
    Decimal values are stored as strings inside the JSON to preserve precision.
    """

    __tablename__ = "billing_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind = Column(
        Enum("devis", "facture", name="billing_document_kind", create_type=False),
        nullable=False,
    )
    document_number = Column(String(32), nullable=False)
    status = Column(
        Enum(
            "draft",
            "sent",
            "accepted",
            "rejected",
            "expired",
            "paid",
            "overdue",
            "cancelled",
            name="billing_document_status",
            create_type=False,
        ),
        nullable=False,
        server_default="draft",
    )
    issue_date = Column(Date, nullable=False)
    validity_until = Column(Date, nullable=True)
    payment_due_date = Column(Date, nullable=True)
    payment_terms = Column(Text, nullable=True)

    recipient_name = Column(String(255), nullable=False)
    recipient_address = Column(Text, nullable=True)
    recipient_email = Column(String(255), nullable=True)
    recipient_siret = Column(String(32), nullable=True)

    notes = Column(Text, nullable=True)
    terms = Column(Text, nullable=True)
    signature_block_text = Column(Text, nullable=True)

    items = Column(ItemsJSON, nullable=False, default=list)

    # Issuer snapshot (copied from CompanyProfile at create time)
    issuer_legal_name = Column(String(255), nullable=False)
    issuer_address = Column(Text, nullable=False)
    issuer_siret = Column(String(32), nullable=True)
    issuer_tva_number = Column(String(32), nullable=True)
    issuer_iban = Column(String(64), nullable=True)
    issuer_bic = Column(String(32), nullable=True)
    issuer_logo_url = Column(Text, nullable=True)

    # Company reference — populated at document create time.
    # NULL for legacy documents created before the companies module migration.
    # ON DELETE SET NULL: deleting a company preserves historical documents
    # (issuer_* snapshot columns already capture the needed data for PDFs).
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )

    source_devis_id = Column(
        UUID(as_uuid=True),
        ForeignKey("billing_documents.id", ondelete="SET NULL"),
        nullable=True,
    )

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

    # Relationships
    user = relationship("UserModel", foreign_keys=[user_id])
    project = relationship("ProjectModel", foreign_keys=[project_id])
    company = relationship("CompanyModel", foreign_keys=[company_id])
    source_devis = relationship(
        "BillingDocumentModel",
        foreign_keys=[source_devis_id],
        remote_side="BillingDocumentModel.id",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "kind",
            "document_number",
            name="uq_billing_document_user_kind_number",
        ),
        CheckConstraint(
            "kind = 'devis' OR validity_until IS NULL",
            name="ck_billing_doc_validity_until_devis_only",
        ),
        CheckConstraint(
            "kind = 'facture' OR (payment_due_date IS NULL AND payment_terms IS NULL)",
            name="ck_billing_doc_payment_fields_facture_only",
        ),
        # Partial indexes are declared in the migration; include a composite
        # index here for SQLAlchemy schema reflection completeness.
        Index(
            "ix_billing_documents_user_kind_status",
            "user_id",
            "kind",
            "status",
        ),
    )

    def __repr__(self) -> str:
        return f"<BillingDocument {self.document_number} kind={self.kind} status={self.status}>"
