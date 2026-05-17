"""Invoice database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Column, Date, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
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
        Enum("released_funds", "labor", "supplier", name="invoicetype", create_type=False),
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

    project = relationship("ProjectModel", foreign_keys=[project_id])
    creator = relationship("UserModel", foreign_keys=[created_by])
    payment_method = relationship("PaymentMethodModel", foreign_keys=[payment_method_id])

    __table_args__ = (UniqueConstraint("project_id", "invoice_number", name="uq_project_invoice_number"),)

    def __repr__(self) -> str:
        return f"<Invoice {self.invoice_number} project={self.project_id}>"
