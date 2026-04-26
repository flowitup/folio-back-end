"""Use case: upload a file attachment to an invoice."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import BinaryIO, Optional
from uuid import UUID, uuid4

from app.application.invoice.ports import IAttachmentStorage, IInvoiceAttachmentRepository, IInvoiceRepository
from app.domain.entities.invoice_attachment import InvoiceAttachment
from app.domain.exceptions.invoice_exceptions import InvoiceNotFoundError

# Validation constants
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


class FileTooLargeError(ValueError):
    """Raised when uploaded file exceeds the size limit."""


class UnsupportedFileTypeError(ValueError):
    """Raised when uploaded file's MIME type is not in the whitelist."""


class UploadAttachmentUseCase:
    """Validates, stores file in S3, and persists metadata row."""

    def __init__(
        self,
        invoice_repository: IInvoiceRepository,
        attachment_repository: IInvoiceAttachmentRepository,
        storage: IAttachmentStorage,
    ) -> None:
        self._invoices = invoice_repository
        self._attachments = attachment_repository
        self._storage = storage

    def execute(
        self,
        invoice_id: UUID,
        filename: str,
        mime_type: str,
        size_bytes: int,
        fileobj: BinaryIO,
        uploaded_by: Optional[UUID] = None,
    ) -> InvoiceAttachment:
        # Existence check — fail fast before touching S3
        invoice = self._invoices.find_by_id(invoice_id)
        if invoice is None:
            raise InvoiceNotFoundError(f"Invoice {invoice_id} not found")

        if size_bytes > MAX_FILE_SIZE_BYTES:
            raise FileTooLargeError(f"File exceeds {MAX_FILE_SIZE_BYTES} bytes (got {size_bytes})")

        if mime_type not in ALLOWED_MIME_TYPES:
            raise UnsupportedFileTypeError(f"MIME type '{mime_type}' is not allowed")

        attachment_id = uuid4()
        # Sanitize filename for the storage key — keep only the basename, no path separators
        safe_name = filename.replace("/", "_").replace("\\", "_")
        storage_key = f"invoice-attachments/{invoice_id}/{attachment_id}/{safe_name}"

        # Upload first; only persist metadata if storage succeeds
        self._storage.put(storage_key, fileobj, content_type=mime_type)

        attachment = InvoiceAttachment(
            id=attachment_id,
            invoice_id=invoice_id,
            filename=filename,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            uploaded_by=uploaded_by,
            uploaded_at=datetime.now(timezone.utc),
        )
        try:
            return self._attachments.save(attachment)
        except Exception:
            # Roll back the orphaned S3 object so we don't leak storage on DB failure.
            try:
                self._storage.delete(storage_key)
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "Failed to clean up orphaned S3 object %s after DB save failure", storage_key
                )
            raise
