"""Adapter implementing FundsReleasePort — cross-BC bridge from billing to invoice."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.value_objects.invoice_item import InvoiceItem

if TYPE_CHECKING:
    from app.application.invoice.ports import IInvoiceRepository

log = logging.getLogger(__name__)


class FundsReleaseAdapter:
    """Create/delete released_funds invoices triggered by billing facture status changes."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._invoice_repo = invoice_repo

    def create_funds_release(
        self,
        project_id: UUID,
        source_doc_id: UUID,
        amount_items: list,
        recipient_name: str,
        issue_date: date,
        created_by: UUID,
    ) -> None:
        invoice_number = self._invoice_repo.next_funds_release_number(project_id)
        now = datetime.now(timezone.utc)

        items = [
            InvoiceItem(
                description=it.get("description", ""),
                quantity=Decimal(str(it.get("quantity", 1))),
                unit_price=Decimal(str(it.get("unit_price", 0))),
            )
            for it in amount_items
        ]

        invoice = Invoice(
            id=uuid4(),
            project_id=project_id,
            invoice_number=invoice_number,
            type=InvoiceType.RELEASED_FUNDS,
            issue_date=issue_date,
            recipient_name=recipient_name,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            items=items,
            source_billing_document_id=source_doc_id,
            is_auto_generated=True,
        )

        try:
            self._invoice_repo.create(invoice)
        except IntegrityError:
            log.warning(
                "Funds release already exists for billing document %s — skipping duplicate",
                source_doc_id,
            )

    def delete_funds_release(self, source_doc_id: UUID) -> None:
        self._invoice_repo.delete_by_source_billing_document_id(source_doc_id)
