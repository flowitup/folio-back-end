"""Use cases for listing, downloading, renaming, and deleting invoice attachments."""

from __future__ import annotations

import os
from typing import BinaryIO, Optional
from uuid import UUID

from app.application.invoice.ports import IAttachmentStorage, IInvoiceAttachmentRepository, IInvoiceRepository
from app.domain.entities.invoice import Invoice, RefundableStatus
from app.domain.entities.invoice_attachment import MAX_ATTACHMENT_FILENAME_LENGTH, InvoiceAttachment
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError
from app.domain.value_objects.display_filename import has_control_chars


class AttachmentNotFoundError(LookupError):
    """Raised when an attachment id does not match any record."""


def assert_attachments_editable(invoice: Optional[Invoice]) -> None:
    """A refunded expense is locked (see UpdateInvoiceUseCase): so is the receipt it was refunded on.

    Otherwise a manager could delete or swap the proof of an expense an admin already approved.
    """
    if invoice is not None and getattr(invoice, "refundable_status", None) == RefundableStatus.REFUNDED.value:
        raise InvalidInvoiceDataError("Refunded expenses are locked; clear the refund status first")


class ListAttachmentsUseCase:
    def __init__(self, attachment_repository: IInvoiceAttachmentRepository) -> None:
        self._attachments = attachment_repository

    def execute(self, invoice_id: UUID) -> list[InvoiceAttachment]:
        return self._attachments.list_by_invoice(invoice_id)


class GetAttachmentUseCase:
    """Returns metadata; the route streams the file content separately via storage."""

    def __init__(
        self,
        attachment_repository: IInvoiceAttachmentRepository,
        storage: IAttachmentStorage,
    ) -> None:
        self._attachments = attachment_repository
        self._storage = storage

    def execute(self, attachment_id: UUID) -> tuple[InvoiceAttachment, BinaryIO, int]:
        att = self._attachments.find_by_id(attachment_id)
        if att is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} not found")
        stream, length = self._storage.get_stream(att.storage_key)
        return att, stream, length


class RenameAttachmentUseCase:
    """Rename an attachment's display filename.

    Only the ``filename`` metadata changes — the object-store ``storage_key`` is
    untouched, so the file content and download URL stay valid. The new filename
    must preserve the original extension (case-insensitive) so ``mime_type`` and
    inline-preview behaviour remain consistent.
    """

    def __init__(
        self,
        attachment_repository: IInvoiceAttachmentRepository,
        invoice_repository: Optional[IInvoiceRepository] = None,
    ) -> None:
        self._attachments = attachment_repository
        self._invoices = invoice_repository

    def execute(self, attachment_id: UUID, new_filename: str) -> InvoiceAttachment:
        att = self._attachments.find_by_id(attachment_id)
        if att is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} not found")
        if self._invoices is not None:
            assert_attachments_editable(self._invoices.find_by_id(att.invoice_id))

        new_filename = new_filename.strip()
        if not new_filename:
            raise ValueError("Filename cannot be empty")
        if len(new_filename) > MAX_ATTACHMENT_FILENAME_LENGTH:
            raise ValueError(f"Filename is too long (max {MAX_ATTACHMENT_FILENAME_LENGTH} characters)")
        if has_control_chars(new_filename):
            # It would end up in the download's Content-Disposition header
            raise ValueError("Filename cannot contain line breaks or other control characters")

        original_ext = os.path.splitext(att.filename)[1].lower()
        new_ext = os.path.splitext(new_filename)[1].lower()
        if new_ext != original_ext:
            raise ValueError(f"File extension must remain {original_ext}")

        self._attachments.update_filename(attachment_id, new_filename)

        updated = self._attachments.find_by_id(attachment_id)
        if updated is None:  # pragma: no cover — row just updated in same txn
            raise AttachmentNotFoundError(f"Attachment {attachment_id} not found")
        return updated


class DeleteAttachmentUseCase:
    def __init__(
        self,
        attachment_repository: IInvoiceAttachmentRepository,
        storage: IAttachmentStorage,
        invoice_repository: Optional[IInvoiceRepository] = None,
    ) -> None:
        self._attachments = attachment_repository
        self._storage = storage
        self._invoices = invoice_repository

    def execute(self, attachment_id: UUID) -> None:
        att = self._attachments.find_by_id(attachment_id)
        if att is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} not found")
        if self._invoices is not None:
            assert_attachments_editable(self._invoices.find_by_id(att.invoice_id))
        # Best-effort: drop S3 object first, then DB row.
        # If S3 delete fails, surface the error so the caller can retry.
        self._storage.delete(att.storage_key)
        self._attachments.delete(attachment_id)
