"""SQLAlchemy repository adapter for invoice attachments."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.invoice.ports import IInvoiceAttachmentRepository
from app.domain.entities.invoice_attachment import InvoiceAttachment
from app.infrastructure.database.models.invoice_attachment import InvoiceAttachmentModel


def _to_entity(m: InvoiceAttachmentModel) -> InvoiceAttachment:
    return InvoiceAttachment(
        id=m.id,
        invoice_id=m.invoice_id,
        filename=m.filename,
        storage_key=m.storage_key,
        mime_type=m.mime_type,
        size_bytes=m.size_bytes,
        uploaded_by=m.uploaded_by,
        uploaded_at=m.uploaded_at,
    )


class SQLAlchemyInvoiceAttachmentRepository(IInvoiceAttachmentRepository):
    """Persistence adapter for invoice attachment metadata."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, attachment: InvoiceAttachment) -> InvoiceAttachment:
        model = InvoiceAttachmentModel(
            id=attachment.id,
            invoice_id=attachment.invoice_id,
            filename=attachment.filename,
            storage_key=attachment.storage_key,
            mime_type=attachment.mime_type,
            size_bytes=attachment.size_bytes,
            uploaded_by=attachment.uploaded_by,
            uploaded_at=attachment.uploaded_at,
        )
        self._session.add(model)
        self._session.commit()
        return _to_entity(model)

    def find_by_id(self, attachment_id: UUID) -> Optional[InvoiceAttachment]:
        m = self._session.query(InvoiceAttachmentModel).filter_by(id=attachment_id).first()
        return _to_entity(m) if m else None

    def list_by_invoice(self, invoice_id: UUID) -> list[InvoiceAttachment]:
        rows = (
            self._session.query(InvoiceAttachmentModel)
            .filter_by(invoice_id=invoice_id)
            .order_by(InvoiceAttachmentModel.uploaded_at.desc())
            .all()
        )
        return [_to_entity(m) for m in rows]

    def delete(self, attachment_id: UUID) -> bool:
        m = self._session.query(InvoiceAttachmentModel).filter_by(id=attachment_id).first()
        if not m:
            return False
        self._session.delete(m)
        self._session.commit()
        return True
