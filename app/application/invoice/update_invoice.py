"""Update invoice use case."""

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, InvoiceNotFoundError
from app.domain.value_objects.invoice_item import InvoiceItem


@dataclass
class UpdateInvoiceRequest:
    invoice_id: UUID
    recipient_name: Optional[str] = None
    issue_date: Optional[date] = None
    items: Optional[list] = None  # list of dicts; type is immutable after creation
    recipient_address: Optional[str] = None
    notes: Optional[str] = None


class UpdateInvoiceUseCase:
    """Partially update an existing invoice (type is immutable)."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, request: UpdateInvoiceRequest) -> InvoiceResponse:
        invoice = self._repo.find_by_id(request.invoice_id)
        if not invoice:
            raise InvoiceNotFoundError(f"Invoice {request.invoice_id} not found")

        updates: dict = {"updated_at": datetime.now(timezone.utc)}

        if request.recipient_name is not None:
            name = request.recipient_name.strip()
            if not name:
                raise InvalidInvoiceDataError("Recipient name is required")
            updates["recipient_name"] = name

        if request.issue_date is not None:
            updates["issue_date"] = request.issue_date

        if request.recipient_address is not None:
            updates["recipient_address"] = request.recipient_address

        if request.notes is not None:
            updates["notes"] = request.notes

        if request.items is not None:
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
            updates["items"] = invoice_items

        updated = dataclasses.replace(invoice, **updates)
        saved = self._repo.update(updated)
        return InvoiceResponse.from_entity(saved)
