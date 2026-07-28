"""Adapter implementing FundsReleasePort — cross-BC bridge from billing to invoice."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.exceptions.invoice_exceptions import InvoiceNumberConflictError
from app.domain.value_objects.invoice_item import InvoiceItem

if TYPE_CHECKING:
    from app.application.invoice.ports import IInvoiceRepository

log = logging.getLogger(__name__)

# Two concurrent PATCHes (or one double-click) can both read the same "next"
# sequential FR number before either commits; the loser's insert fails with
# InvoiceNumberConflictError (translated from the uq_project_invoice_number
# IntegrityError by SQLAlchemyInvoiceRepository.create). Regenerating the
# number and retrying resolves it without surfacing a 500 to the caller.
_MAX_NUMBER_CONFLICT_ATTEMPTS = 3


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
                vat_rate=Decimal(str(it.get("vat_rate", 0))),
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

    def create_bank_refund_release(self, source: Invoice, created_by: UUID) -> None:
        """Create the auto-generated released_funds release for a bank-refunded expense.

        Idempotent: no-op if a bank-refund release already exists for source.id.
        No-op when source.type is not materials_services. No-op (logged at INFO)
        when source.total_amount <= 0 — a zero/negative inflow would corrupt
        sum_funds_released. Races with the partial unique index
        (uq_invoices_bank_refund_release) are caught as IntegrityError and
        logged, mirroring create_funds_release. A concurrent request claiming
        the same sequential FR number surfaces as InvoiceNumberConflictError
        instead (already translated by the repository) — retried up to
        _MAX_NUMBER_CONFLICT_ATTEMPTS times with a freshly generated number,
        then re-raised for the caller to map to an HTTP response.
        """
        if source.type != InvoiceType.MATERIALS_SERVICES:
            return
        if source.total_amount <= 0:
            log.info(
                "Skipping bank-refund release for invoice %s — total_amount <= 0",
                source.id,
            )
            return
        if self._invoice_repo.find_bank_refund_release(source.id) is not None:
            return

        now = datetime.now(timezone.utc)
        items = [
            InvoiceItem(
                description=f"Remboursement banque — {source.invoice_number}",
                quantity=Decimal("1"),
                unit_price=source.total_amount,
                vat_rate=Decimal("0"),
            )
        ]

        for attempt in range(1, _MAX_NUMBER_CONFLICT_ATTEMPTS + 1):
            invoice_number = self._invoice_repo.next_funds_release_number(
                source.project_id, year=source.issue_date.year
            )
            release = Invoice(
                id=uuid4(),
                project_id=source.project_id,
                invoice_number=invoice_number,
                type=InvoiceType.RELEASED_FUNDS,
                issue_date=source.issue_date,
                recipient_name=source.recipient_name,
                created_by=created_by,
                created_at=now,
                updated_at=now,
                items=items,
                refunds_invoice_id=source.id,
                is_auto_generated=True,
            )

            try:
                self._invoice_repo.create(release)
                return
            except IntegrityError as exc:
                log.warning(
                    "Bank-refund release insert for source %s rejected by a unique constraint: %s",
                    source.id,
                    exc,
                )
                return
            except InvoiceNumberConflictError:
                if attempt == _MAX_NUMBER_CONFLICT_ATTEMPTS:
                    raise
                log.warning(
                    "Invoice number conflict creating bank-refund release for source %s "
                    "(attempt %d/%d) — retrying with a new number",
                    source.id,
                    attempt,
                    _MAX_NUMBER_CONFLICT_ATTEMPTS,
                )

    def delete_bank_refund_release(self, source_id: UUID) -> None:
        """Delete the auto-generated bank-refund release linked to source_id, if any."""
        self._invoice_repo.delete_bank_refund_release(source_id)
