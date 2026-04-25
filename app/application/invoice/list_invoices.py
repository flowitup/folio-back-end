"""List invoices use case."""

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.domain.entities.invoice import InvoiceType


@dataclass
class ListInvoicesRequest:
    project_id: UUID
    invoice_type: Optional[InvoiceType] = None  # filter by type


class ListInvoicesUseCase:
    """List all invoices for a project, optionally filtered by type."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, request: ListInvoicesRequest) -> list:
        invoices = self._repo.list_by_project(request.project_id, request.invoice_type)
        return [InvoiceResponse.from_entity(inv) for inv in invoices]
