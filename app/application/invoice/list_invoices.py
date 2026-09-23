"""List invoices use case."""

from dataclasses import dataclass
from datetime import date
from typing import Optional
from uuid import UUID

from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.domain.entities.invoice import InvoiceType


@dataclass
class ListInvoicesRequest:
    project_id: UUID
    invoice_type: Optional[InvoiceType] = None  # filter by type
    # Labor payments drill-down filters — compose with invoice_type.
    service_month: Optional[date] = None  # exact match on labor rows (first-of-month)
    worker_id: Optional[UUID] = None  # exact match on the invoice's linked worker


class ListInvoicesUseCase:
    """List all invoices for a project, optionally filtered by type/service_month/worker_id."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, request: ListInvoicesRequest) -> list:
        # The type filter matches the LEDGER type: a cash advance is stored as
        # released_funds but listed under others. The repository filters on the
        # stored type, so an OTHERS request loads every type and narrows below.
        repo_type = None if request.invoice_type == InvoiceType.OTHERS else request.invoice_type
        invoices = self._repo.list_by_project(
            request.project_id,
            repo_type,
            service_month=request.service_month,
            worker_id=request.worker_id,
        )
        if request.invoice_type is not None:
            invoices = [inv for inv in invoices if inv.ledger_type == request.invoice_type]
        return [InvoiceResponse.from_entity(inv) for inv in invoices]
