"""Create invoice use case."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.domain.entities.invoice import HIGHLIGHT_COLORS, Invoice, InvoiceType, MIXED_SIGN_TYPES, SettledVia
from app.domain.exceptions.invoice_exceptions import (
    AppliedAmountExceedsTargetError,
    CashAdvanceNotAllowedError,
    InvalidInvoiceDataError,
    RefundExceedsSourceError,
    ServiceMonthNotAllowedError,
    WorkerLinkNotAllowedError,
    WorkerNotInProjectError,
)
from app.domain.payment_methods.exceptions import PaymentMethodNotActiveError, PaymentMethodNotFoundError
from app.domain.value_objects.invoice_item import InvoiceItem

# Allowed settled_via string values (mirrors SettledVia enum members). Validated
# here too — not only at the Pydantic schema boundary — so any direct/internal
# caller of the use-case still gets the invariant enforced.
_VALID_SETTLED_VIA = {v.value for v in SettledVia}


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
    # Supplier-refund link — optional; only valid when type == REFUND.
    refunds_invoice_id: Optional[UUID] = None
    # Payment month for labor invoices — optional; only valid when type == LABOR.
    # Normalized to day=1 by the use-case.
    service_month: Optional[date] = None
    # How this return is settled ('cash' | 'avoir') — optional; only valid when type == RETURN.
    settled_via: Optional[str] = None
    # Avoir-only link: the invoice this return's credit is applied to — optional; only
    # valid when type == RETURN and settled_via == 'avoir'.
    applied_to_invoice_id: Optional[UUID] = None
    # Worker link — optional; only valid when type == LABOR. When set, the
    # use-case snapshots recipient_name from the worker's display name.
    worker_id: Optional[UUID] = None
    # Optional row-highlight color — one of HIGHLIGHT_COLORS or None (no highlight).
    # Applies to every invoice type; validated against the palette.
    highlight_color: Optional[str] = None
    # Company cash advance — only valid when type == RELEASED_FUNDS. Marks the
    # release as company money handed to a person (excluded from released totals).
    is_cash_advance: bool = False


class CreateInvoiceUseCase:
    """Create a new invoice for a project."""

    def __init__(
        self,
        invoice_repo: IInvoiceRepository,
        payment_method_repo: object = None,  # IPaymentMethodRepository | None
        worker_reader: object = None,  # WorkerReaderPort | None
    ) -> None:
        self._repo = invoice_repo
        self._pm_repo = payment_method_repo
        self._worker_reader = worker_reader

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

        # Refund link validation + cap (only when refunds_invoice_id is provided).
        refunds_invoice_id: Optional[UUID] = None
        if request.refunds_invoice_id is not None:
            if request.type != InvoiceType.RETURN:
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

        # settled_via / applied_to_invoice_id validation (only valid on type == RETURN).
        if (request.settled_via is not None or request.applied_to_invoice_id is not None) and (
            request.type != InvoiceType.RETURN
        ):
            raise InvalidInvoiceDataError(
                "settled_via and applied_to_invoice_id may only be set on invoices of type 'return'"
            )

        settled_via: Optional[str] = None
        if request.settled_via is not None:
            if request.settled_via not in _VALID_SETTLED_VIA:
                raise InvalidInvoiceDataError(f"settled_via must be one of: {', '.join(sorted(_VALID_SETTLED_VIA))}")
            settled_via = request.settled_via

        applied_to_invoice_id: Optional[UUID] = None
        if request.applied_to_invoice_id is not None:
            # applied_to_invoice_id means "consumed as payment" — only meaningful when
            # the return is settled as store credit, not a straight cash refund.
            if settled_via != SettledVia.AVOIR.value:
                raise InvalidInvoiceDataError("applied_to_invoice_id may only be set when settled_via is 'avoir'")
            target = self._repo.find_by_id(request.applied_to_invoice_id)
            if target is None:
                raise InvalidInvoiceDataError(f"Target invoice {request.applied_to_invoice_id} not found")
            if target.project_id != request.project_id:
                raise InvalidInvoiceDataError("applied_to_invoice_id must reference an invoice in the same project")
            if target.type in (InvoiceType.RETURN, InvoiceType.RELEASED_FUNDS):
                raise InvalidInvoiceDataError(
                    "applied_to_invoice_id must reference an invoice that is not a return or released_funds"
                )
            # Cap: abs(Σ applied returns incl. this one) must stay <= target total.
            # Race (accepted, not locked): this is a read-then-write check with no
            # row/advisory lock, so two concurrent creates against the same target
            # could both pass and jointly exceed the cap — identical, accepted class
            # of drift as the pre-existing refunds_invoice_id cap check above.
            this_total = sum((item.total for item in invoice_items), Decimal("0"))
            existing_applied = self._repo.sum_applied_for_target(target.id)
            total_applied = existing_applied + this_total
            if abs(total_applied) > target.total_amount:
                remaining = target.total_amount - abs(existing_applied)
                raise AppliedAmountExceedsTargetError(
                    f"Applied amount exceeds target invoice total. Remaining applicable: {remaining:.2f}"
                )
            applied_to_invoice_id = target.id
            # Auto-align: server-side copy of the target's payment method, even when
            # NULL — an avoir return is settled by consuming the target invoice's
            # payment record, so it must mirror the target exactly regardless of
            # whatever payment_method_id the caller may have also supplied.
            payment_method_id = target.payment_method_id
            payment_method_label = target.payment_method_label

        # service_month is only valid on labor invoices; normalize any day to day=1.
        service_month: Optional[date] = None
        if request.service_month is not None:
            if request.type != InvoiceType.LABOR:
                raise ServiceMonthNotAllowedError("service_month may only be set on invoices of type 'labor'")
            service_month = request.service_month.replace(day=1)

        # worker_id is only valid on labor invoices; the worker must belong to this
        # project. When set, the resolved display name overrides the client-sent
        # recipient_name (server-side snapshot, not just a default).
        worker_id: Optional[UUID] = None
        if request.worker_id is not None:
            if request.type != InvoiceType.LABOR:
                raise WorkerLinkNotAllowedError("worker_id may only be set on invoices of type 'labor'")
            if self._worker_reader is None:
                raise InvalidInvoiceDataError("Worker link support is not available")
            worker = self._worker_reader.get_for_project(request.worker_id, request.project_id)
            if worker is None:
                raise WorkerNotInProjectError(f"Worker {request.worker_id} not found in this project")
            worker_id = worker.id
            name = worker.display_name

        # highlight_color is optional and palette-validated; applies to any type.
        if request.highlight_color is not None and request.highlight_color not in HIGHLIGHT_COLORS:
            raise InvalidInvoiceDataError(f"highlight_color must be one of: {', '.join(sorted(HIGHLIGHT_COLORS))}")

        # is_cash_advance only makes sense on a released_funds row: it reclassifies a
        # release as company money handed to a person. The spend aggregations ignore
        # it on expense rows, so a stray flag there would only mislead readers.
        if request.is_cash_advance and request.type != InvoiceType.RELEASED_FUNDS:
            raise CashAdvanceNotAllowedError("is_cash_advance may only be set on invoices of type 'released_funds'")

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
            refunds_invoice_id=refunds_invoice_id,
            service_month=service_month,
            settled_via=settled_via,
            applied_to_invoice_id=applied_to_invoice_id,
            worker_id=worker_id,
            highlight_color=request.highlight_color,
            is_cash_advance=bool(request.is_cash_advance),
        )

        saved = self._repo.create(invoice)
        return InvoiceResponse.from_entity(saved)
