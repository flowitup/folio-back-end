"""Invoice attachment database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class InvoiceAttachmentModel(Base):
    """ORM model for invoice_attachments — metadata only; file lives in S3."""

    __tablename__ = "invoice_attachments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    storage_key = Column(String(512), nullable=False, unique=True)
    mime_type = Column(String(127), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    uploaded_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    invoice = relationship("InvoiceModel", foreign_keys=[invoice_id])
    uploader = relationship("UserModel", foreign_keys=[uploaded_by])

    def __repr__(self) -> str:
        return f"<InvoiceAttachment {self.filename} invoice={self.invoice_id}>"
