"""Create invoice use case."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.application.tags.exceptions import InvalidProjectTagError
from app.domain.entities.invoice import Invoice, InvoiceType, MIXED_SIGN_TYPES
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, RefundExceedsSourceError
from app.domain.payment_methods.exceptions import PaymentMethodNotActiveError, PaymentMethodNotFoundError
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
    payment_method_id: Optional[UUID] = None
    # company_id is used to cross-validate that the payment method belongs to
    # the same company as the invoice's project. Optional: when None, the
    # cross-company check is skipped (e.g. payment_method_repo unavailable).
    company_id: Optional[UUID] = None
    # Phase tag — optional; validated (project scope) in the route layer.
    tag_id: Optional[UUID] = None
    # Supplier-refund link — optional; only valid when type == REFUND.
    refunds_invoice_id: Optional[UUID] = None


class CreateInvoiceUseCase:
    """Create a new invoice for a project."""

    def __init__(
        self,
        invoice_repo: IInvoiceRepository,
        payment_method_repo: object = None,  # IPaymentMethodRepository | None
        tag_repo=None,  # ProjectTagRepositoryPort | None
    ) -> None:
        self._repo = invoice_repo
        self._pm_repo = payment_method_repo
        self._tag_repo = tag_repo

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
            vat = Decimal(str(raw.get("vat_rate", 0)))
            desc = str(raw.get("description", "")).strip()
            if not desc:
                raise InvalidInvoiceDataError("Item description is required")
            if qty <= 0:
                raise InvalidInvoiceDataError("Item quantity must be greater than 0")
            # Mixed-sign unit_price is allowed only for materials_services and refund.
            # For all other types (labor, others, released_funds) price must be >= 0.
            if price < 0 and request.type not in MIXED_SIGN_TYPES:
                raise InvalidInvoiceDataError(
                    f"Item unit_price cannot be negative for invoice type '{request.type.value}'"
                )
            invoice_items.append(InvoiceItem(description=desc, quantity=qty, unit_price=price, vat_rate=vat))

        # Resolve payment method if provided
        payment_method_id: Optional[UUID] = None
        payment_method_label: Optional[str] = None

        if request.payment_method_id is not None:
            if self._pm_repo is None:
                raise InvalidInvoiceDataError("Payment method support is not available")

            method = self._pm_repo.find_by_id_for_update(request.payment_method_id)
            if method is None:
                raise PaymentMethodNotFoundError(request.payment_method_id)
            if not method.is_active:
                raise PaymentMethodNotActiveError(request.payment_method_id)
            # Cross-company guard: method must belong to the invoice's company
            if request.company_id is not None and method.company_id != request.company_id:
                from app.domain.companies.exceptions import ForbiddenCompanyError

                raise ForbiddenCompanyError(request.created_by, request.company_id)

            payment_method_id = method.id
            payment_method_label = method.label

        # Guard: tag must belong to the same project as the invoice.
        if request.tag_id is not None and self._tag_repo is not None:
            tag = self._tag_repo.get_by_id(request.tag_id)
            if tag is None or tag.project_id != request.project_id:
                raise InvalidProjectTagError(f"Tag {request.tag_id} does not belong to this project")

        # Refund link validation + cap (only when refunds_invoice_id is provided).
        refunds_invoice_id: Optional[UUID] = None
        if request.refunds_invoice_id is not None:
            if request.type != InvoiceType.REFUND:
                raise InvalidInvoiceDataError("refunds_invoice_id may only be set on invoices of type 'refund'")
            source = self._repo.find_by_id(request.refunds_invoice_id)
            if source is None:
                raise InvalidInvoiceDataError(f"Source invoice {request.refunds_invoice_id} not found")
            if source.project_id != request.project_id:
                raise InvalidInvoiceDataError("refunds_invoice_id must reference an invoice in the same project")
            if source.type != InvoiceType.MATERIALS_SERVICES:
                raise InvalidInvoiceDataError("refunds_invoice_id must reference a materials_services invoice")
            # Cap: source.total + Σ(linked refunds) + this_total must remain >= 0.
            this_total = sum((item.total for item in invoice_items), Decimal("0"))
            existing_refunds = self._repo.sum_refunds_for_source(source.id)
            remaining = source.total_amount + existing_refunds
            if remaining + this_total < 0:
                raise RefundExceedsSourceError(
                    f"Refund exceeds source invoice amount. Remaining refundable: {remaining:.2f}"
                )
            refunds_invoice_id = request.refunds_invoice_id

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
            payment_method_id=payment_method_id,
            payment_method_label=payment_method_label,
            tag_id=request.tag_id,
            refunds_invoice_id=refunds_invoice_id,
        )

        saved = self._repo.create(invoice)
        return InvoiceResponse.from_entity(saved)
