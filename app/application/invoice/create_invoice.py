"""Create invoice use case."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError
from app.domain.value_objects.invoice_item import InvoiceItem


@dataclass
class CreateInvoiceRequest:
    project_id: UUID
    created_by: UUID
    type: InvoiceType
    issue_date: date
    recipient_name: str
    items: list  # list of dicts: {description, quantity, unit_price}
    recipient_address: Optional[str] = None
    notes: Optional[str] = None


class CreateInvoiceUseCase:
    """Create a new invoice for a project."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, request: CreateInvoiceRequest) -> InvoiceResponse:
        # Validate recipient
        name = request.recipient_name.strip() if request.recipient_name else ""
        if not name:
            raise InvalidInvoiceDataError("Recipient name is required")

        # Validate and build items
        if not request.items:
            raise InvalidInvoiceDataError("At least one line item is required")

        invoice_items = []
        for raw in request.items:
            qty = Decimal(str(raw.get("quantity", 0)))
            price = Decimal(str(raw.get("unit_price", 0)))
            desc = str(raw.get("description", "")).strip()
            if not desc:
                raise InvalidInvoiceDataError("Item description is required")
            if qty <= 0:
                raise InvalidInvoiceDataError("Item quantity must be greater than 0")
            if price < 0:
                raise InvalidInvoiceDataError("Item unit_price cannot be negative")
            invoice_items.append(InvoiceItem(description=desc, quantity=qty, unit_price=price))

        # Generate invoice number via repo
        invoice_number = self._repo.next_invoice_number(request.project_id)

        now = datetime.now(timezone.utc)
        invoice = Invoice(
            id=uuid4(),
            project_id=request.project_id,
            invoice_number=invoice_number,
            type=request.type,
            issue_date=request.issue_date,
            recipient_name=name,
            recipient_address=request.recipient_address,
            notes=request.notes,
            items=invoice_items,
            created_by=request.created_by,
            created_at=now,
            updated_at=now,
        )

        saved = self._repo.create(invoice)
        return InvoiceResponse.from_entity(saved)
