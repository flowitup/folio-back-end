"""Delete invoice use case."""

from uuid import UUID

from app.application.invoice.ports import IInvoiceRepository
from app.domain.exceptions.invoice_exceptions import InvoiceNotFoundError


class DeleteInvoiceUseCase:
    """Delete an invoice by ID, raising if not found."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, invoice_id: UUID) -> None:
        invoice = self._repo.find_by_id(invoice_id)
        if not invoice:
            raise InvoiceNotFoundError(f"Invoice {invoice_id} not found")
        self._repo.delete(invoice_id)
