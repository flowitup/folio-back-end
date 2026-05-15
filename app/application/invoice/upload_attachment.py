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

# Number of bytes sufficient to identify all magic-byte signatures below.
# Read once from the stream and rewind — keeps the upload single-pass.
_MAGIC_PEEK_BYTES = 16


def _matches_magic(mime_type: str, head: bytes) -> bool:
    """Validate that ``head`` (the first bytes of the file) matches the
    signature expected for the claimed MIME.

    Returning True for MIMEs we cannot cheaply detect (HEIC/HEIF) keeps
    the existing whitelist behavior — the relevant attack surface is the
    common formats a browser will render inline (PDF/PNG/JPEG/WEBP),
    which we cover here.
    """
    if mime_type == "application/pdf":
        return head.startswith(b"%PDF-")
    if mime_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP"
    # HEIC/HEIF use a variable "ftyp" box; trust the MIME for those.
    if mime_type in ("image/heic", "image/heif"):
        return True
    return False


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

        # Magic-byte check: client-supplied MIME is untrusted (a user can upload
        # HTML/SVG declaring image/png). Reject any file whose first bytes do
        # not match the claimed format so downstream consumers cannot be
        # tricked into rendering attacker-controlled content.
        try:
            head = fileobj.read(_MAGIC_PEEK_BYTES)
        finally:
            try:
                fileobj.seek(0)
            except Exception:
                # Non-seekable streams are not expected in this code path
                # (Werkzeug's SpooledTemporaryFile supports seek); treat as
                # invalid rather than silently accept.
                raise UnsupportedFileTypeError("Upload stream is not seekable")
        if not _matches_magic(mime_type, head):
            raise UnsupportedFileTypeError(f"File contents do not match declared type '{mime_type}'")

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
