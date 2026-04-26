"""Delete invoice use case."""

import logging
from typing import Optional
from uuid import UUID

from app.application.invoice.ports import (
    IAttachmentStorage,
    IInvoiceAttachmentRepository,
    IInvoiceRepository,
)
from app.domain.exceptions.invoice_exceptions import InvoiceNotFoundError

logger = logging.getLogger(__name__)


class DeleteInvoiceUseCase:
    """Delete an invoice and clean up its attachment files.

    DB-level FK CASCADE removes attachment rows when the invoice row is deleted,
    but it cannot reach into S3 — so we enumerate attachments and delete the
    object-store files first. If the storage delete fails, we log and continue
    so a transient S3 outage does not block invoice deletion (orphans are
    recoverable; a stuck invoice row is not).
    """

    def __init__(
        self,
        invoice_repo: IInvoiceRepository,
        attachment_repo: Optional[IInvoiceAttachmentRepository] = None,
        storage: Optional[IAttachmentStorage] = None,
    ) -> None:
        self._repo = invoice_repo
        self._attachments = attachment_repo
        self._storage = storage

    def execute(self, invoice_id: UUID) -> None:
        invoice = self._repo.find_by_id(invoice_id)
        if not invoice:
            raise InvoiceNotFoundError(f"Invoice {invoice_id} not found")

        # Snapshot attachment keys BEFORE the DB delete so we still have them after
        # FK CASCADE has dropped the rows. This lets us delete the row first
        # (consistency wins on row-delete failure: no metadata-without-files state)
        # and then clean S3 best-effort.
        keys: list[str] = []
        if self._attachments and self._storage:
            keys = [a.storage_key for a in self._attachments.list_by_invoice(invoice_id)]

        self._repo.delete(invoice_id)

        for key in keys:
            try:
                self._storage.delete(key)
            except Exception as exc:
                logger.warning("Failed to delete S3 object %s for invoice %s: %s", key, invoice_id, exc)
