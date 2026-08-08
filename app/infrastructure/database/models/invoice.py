"""Invoice database model."""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
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

# Use JSONB on PostgreSQL, generic JSON elsewhere (SQLite for tests)
ItemsJSON = JSON().with_variant(JSONB(), "postgresql")


class InvoiceModel(Base):
    """SQLAlchemy ORM model for invoices table."""

    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_number = Column(String(20), nullable=False)
    type = Column(
        Enum(
            "released_funds",
            "labor",
            "materials_services",
            "others",
            "return",
            name="invoicetype",
            create_type=False,
        ),
        nullable=False,
        index=True,
    )
    issue_date = Column(Date, nullable=False)
    recipient_name = Column(String(255), nullable=False)
    recipient_address = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    items = Column(ItemsJSON, nullable=False, default=list)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Payment method — optional; NULL for invoices created before the feature
    # was introduced or when no method is recorded.
    # payment_method_label is a snapshot of the label at write-time so that
    # historical invoices still show the correct label even if the method is
    # later renamed or soft-deleted.
    payment_method_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payment_methods.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_method_label = Column(String(120), nullable=True)

    source_billing_document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("billing_documents.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    is_auto_generated = Column(Boolean, nullable=False, default=False, server_default="false")
    # Phase tag — optional; NULL when invoice has no tag. ON DELETE SET NULL keeps
    # the invoice when a tag is removed (financial data must not be lost on tag deletion).
    tag_id = Column(
        UUID(as_uuid=True),
        ForeignKey("project_tags.id", ondelete="SET NULL", name="fk_invoices_tag_id"),
        nullable=True,
        index=True,
    )
    # Refund tracking — optional; NULL means not marked refundable.
    # Only applicable to materials_services invoices; other types must stay NULL.
    refundable_status = Column(String(20), nullable=True)

    # Who issued the refund ('company' | 'bank') — only meaningful when
    # refundable_status == 'refunded'. NULL whenever refundable_status is not 'refunded'.
    refunded_by = Column(String(10), nullable=True)

    # Supplier-refund self-link: a refund invoice may optionally reference the
    # materials_services invoice it refunds. ON DELETE SET NULL so deleting the
    # source invoice nulls the link rather than cascade-deleting the refund.
    refunds_invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL", name="fk_invoices_refunds_invoice_id"),
        nullable=True,
        index=True,
    )

    # Payment month for labor invoices — optional; the calendar month this payment
    # covers. Always normalized to the first day of the month. Only settable when
    # type == 'labor'; NULL for all other invoice types.
    service_month = Column(Date, nullable=True)

    # How this return is settled ('cash' | 'avoir') — only meaningful when type == 'return'.
    # Plain VARCHAR + CHECK constraint rather than a Postgres enum type: adding new
    # allowed values later needs no ALTER TYPE migration.
    settled_via = Column(String(10), nullable=True)

    # Avoir-only self-link: a return invoice may reference the invoice its credit is
    # applied to (a future materials_services/labor/others invoice — never another
    # return or a released_funds release; enforced at the application layer). ON DELETE
    # SET NULL keeps the return standalone if the target invoice is deleted.
    applied_to_invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL", name="fk_invoices_applied_to_invoice_id"),
        nullable=True,
        index=True,
    )

    # Worker link for labor invoices — optional; NULL for non-labor invoices and
    # for labor invoices with no worker recorded. ON DELETE SET NULL keeps the
    # invoice (financial record) intact when the linked worker is deleted.
    worker_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workers.id", ondelete="SET NULL", name="fk_invoices_worker_id"),
        nullable=True,
        index=True,
    )

    project = relationship("ProjectModel", foreign_keys=[project_id])
    creator = relationship("UserModel", foreign_keys=[created_by])
    payment_method = relationship("PaymentMethodModel", foreign_keys=[payment_method_id])
    source_billing_document = relationship("BillingDocumentModel", foreign_keys=[source_billing_document_id])
    tag = relationship("ProjectTagModel", back_populates="invoices", foreign_keys=[tag_id])
    # Self-referential: many refunds may link one source M&S invoice.
    refunds_invoice = relationship(
        "InvoiceModel",
        foreign_keys=[refunds_invoice_id],
        remote_side="InvoiceModel.id",
    )
    # Self-referential: many avoir returns may apply their credit to one target invoice.
    applied_to = relationship(
        "InvoiceModel",
        foreign_keys=[applied_to_invoice_id],
        remote_side="InvoiceModel.id",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "invoice_number", name="uq_project_invoice_number"),
        CheckConstraint("settled_via IN ('cash', 'avoir')", name="ck_invoices_settled_via"),
        # Partial index declared in migration 7e20d88b8197; mirrored here for
        # SQLAlchemy schema reflection completeness (matches the house pattern
        # in BillingDocumentModel.__table_args__). Deliberately postgresql-only:
        # unlike that precedent, a non-partial fallback here would be actively
        # wrong, not just a loose approximation — refunds_invoice_id is shared
        # with type='return' rows, where MULTIPLE return invoices legitimately
        # link to the same source (see sum_refunds_for_source). A plain unique
        # constraint would break that under SQLite tests, so sqlite_where=0
        # declares the index but makes it match no row (always-false), keeping
        # it a schema no-op there instead of silently over-constraining.
        Index(
            "uq_invoices_bank_refund_release",
            "refunds_invoice_id",
            unique=True,
            postgresql_where=sa.text(
                "type = 'released_funds' AND refunds_invoice_id IS NOT NULL AND is_auto_generated"
            ),
            sqlite_where=sa.text("0"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.invoice_number} project={self.project_id}>"
