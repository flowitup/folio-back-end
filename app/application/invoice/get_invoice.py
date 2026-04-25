"""Get invoice by ID use case."""

from uuid import UUID

from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.domain.exceptions.invoice_exceptions import InvoiceNotFoundError


class GetInvoiceUseCase:
    """Retrieve a single invoice by its ID."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, invoice_id: UUID) -> InvoiceResponse:
        invoice = self._repo.find_by_id(invoice_id)
        if not invoice:
            raise InvoiceNotFoundError(f"Invoice {invoice_id} not found")
        return InvoiceResponse.from_entity(invoice)
