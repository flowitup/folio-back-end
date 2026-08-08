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
    tag_id: Optional[UUID] = None  # filter by phase tag (None = no filter)
    # Labor payments drill-down filters — compose with invoice_type/tag_id.
    service_month: Optional[date] = None  # exact match on labor rows (first-of-month)
    worker_id: Optional[UUID] = None  # exact match on the invoice's linked worker


class ListInvoicesUseCase:
    """List all invoices for a project, optionally filtered by type/tag/service_month/worker_id."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, request: ListInvoicesRequest) -> list:
        invoices = self._repo.list_by_project(
            request.project_id,
            request.invoice_type,
            tag_id=request.tag_id,
            service_month=request.service_month,
            worker_id=request.worker_id,
        )
        return [InvoiceResponse.from_entity(inv) for inv in invoices]
