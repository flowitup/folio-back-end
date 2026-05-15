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
from app.domain.payment_methods.exceptions import PaymentMethodNotActiveError, PaymentMethodNotFoundError
from app.domain.value_objects.invoice_item import InvoiceItem

# Sentinel for "field not provided in the PATCH body".
# Distinct from None which means "explicitly set to null / clear".
_UNSET: object = object()


@dataclass
class UpdateInvoiceRequest:
    invoice_id: UUID
    recipient_name: Optional[str] = None
    issue_date: Optional[date] = None
    items: Optional[list] = None  # list of dicts; type is immutable after creation
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    # payment_method_id uses sentinel: _UNSET = not provided, None = clear, UUID = set.
    # Callers that do not provide this field must leave it as _UNSET.
    payment_method_id: object = dataclasses.field(default_factory=lambda: _UNSET)
    # company_id is used to cross-validate that the payment method belongs to
    # the same company as the invoice's project. Optional: when None, skipped.
    company_id: Optional[UUID] = None


class UpdateInvoiceUseCase:
    """Partially update an existing invoice (type is immutable)."""

    def __init__(
        self,
        invoice_repo: IInvoiceRepository,
        payment_method_repo: object = None,  # IPaymentMethodRepository | None
    ) -> None:
        self._repo = invoice_repo
        self._pm_repo = payment_method_repo

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

        # Payment method: only process if the key was explicitly provided.
        if request.payment_method_id is not _UNSET:
            pm_id = request.payment_method_id  # None or UUID

            if pm_id is None:
                # Explicit null → clear both columns
                updates["payment_method_id"] = None
                updates["payment_method_label"] = None
            else:
                # UUID provided → validate and snapshot label
                if self._pm_repo is None:
                    raise InvalidInvoiceDataError("Payment method support is not available")

                method = self._pm_repo.find_by_id_for_update(pm_id)
                if method is None:
                    raise PaymentMethodNotFoundError(pm_id)
                if not method.is_active:
                    raise PaymentMethodNotActiveError(pm_id)
                # Cross-company guard
                if request.company_id is not None and method.company_id != request.company_id:
                    from app.domain.companies.exceptions import ForbiddenCompanyError

                    raise ForbiddenCompanyError(invoice.created_by, request.company_id)

                updates["payment_method_id"] = method.id
                updates["payment_method_label"] = method.label

        updated = dataclasses.replace(invoice, **updates)
        saved = self._repo.update(updated)
        return InvoiceResponse.from_entity(saved)
