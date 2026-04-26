"""Use cases for listing, downloading, and deleting invoice attachments."""

from __future__ import annotations

from typing import BinaryIO
from uuid import UUID

from app.application.invoice.ports import IAttachmentStorage, IInvoiceAttachmentRepository
from app.domain.entities.invoice_attachment import InvoiceAttachment


class AttachmentNotFoundError(LookupError):
    """Raised when an attachment id does not match any record."""


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


class DeleteAttachmentUseCase:
    def __init__(
        self,
        attachment_repository: IInvoiceAttachmentRepository,
        storage: IAttachmentStorage,
    ) -> None:
        self._attachments = attachment_repository
        self._storage = storage

    def execute(self, attachment_id: UUID) -> None:
        att = self._attachments.find_by_id(attachment_id)
        if att is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} not found")
        # Best-effort: drop S3 object first, then DB row.
        # If S3 delete fails, surface the error so the caller can retry.
        self._storage.delete(att.storage_key)
        self._attachments.delete(attachment_id)
