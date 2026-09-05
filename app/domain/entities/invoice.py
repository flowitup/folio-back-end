"""Invoice domain entity."""

import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID


class InvoiceType(str, Enum):
    RELEASED_FUNDS = "released_funds"
    LABOR = "labor"
    MATERIALS_SERVICES = "materials_services"
    OTHERS = "others"
    RETURN = "return"


# Invoice types that allow mixed-sign unit_price (negative = credit/return line).
# All other types require unit_price >= 0.
MIXED_SIGN_TYPES: frozenset = frozenset({InvoiceType.MATERIALS_SERVICES, InvoiceType.RETURN})

# Allowed values for the optional row-highlight color. A fixed palette (stored as
# a plain lowercase string) so the UI can map each name to a tint; NULL = no
# highlight. Applies to invoices of every type. Enforced at the schema and
# use-case layers and mirrored by a DB CHECK constraint.
HIGHLIGHT_COLORS: frozenset = frozenset({"red", "orange", "yellow", "green", "blue", "purple"})


class RefundableStatus(str, Enum):
    """Lifecycle states for company-scoped refund tracking on materials & services expenses."""

    REFUNDABLE = "refundable"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"


class SettledVia(str, Enum):
    """How a return invoice is settled — only meaningful when type == RETURN.

    CASH: the money is refunded directly (e.g. bank/company refund).
    AVOIR: the return's value is applied as credit toward another invoice
    (applied_to_invoice_id) instead of being cashed out.
    """

    CASH = "cash"
    AVOIR = "avoir"


class RefundedBy(str, Enum):
    """Who issued the refund — only meaningful when refundable_status == 'refunded'.

    'both' covers partial reimbursements from each side (per-source amounts are
    not tracked — the flag records involvement, not a split).
    """

    COMPANY = "company"
    BANK = "bank"
    BOTH = "both"


@dataclass(slots=True)
class Invoice:
    """Invoice domain entity. Immutable except for use-case-level updates via dataclasses.replace()."""

    id: UUID
    project_id: UUID
    invoice_number: str
    type: InvoiceType
    issue_date: date
    recipient_name: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    items: list = field(default_factory=list)  # list[InvoiceItem]
    recipient_address: Optional[str] = None
    notes: Optional[str] = None
    # Payment method — optional; NULL for invoices created before the feature.
    # payment_method_label is a snapshot of the label at write-time so historical
    # invoices keep the correct label even after the method is renamed or removed.
    payment_method_id: Optional[UUID] = None
    payment_method_label: Optional[str] = None
    source_billing_document_id: Optional[UUID] = None
    is_auto_generated: bool = False
    # Phase tag — optional; NULL when invoice has no tag assignment.
    tag_id: Optional[UUID] = None
    # Refund tracking — optional; NULL means not marked refundable.
    # Only applicable to materials_services invoices.
    refundable_status: Optional[str] = None
    # Who issued the refund ('company' | 'bank') — only meaningful when
    # refundable_status == 'refunded'. Must be NULL whenever refundable_status
    # is anything else; enforced at the use-case layer, not here.
    refunded_by: Optional[str] = None
    # Optional self-link: return invoice → the materials_services invoice it refunds.
    # SET NULL on deletion of the linked invoice so the return survives as standalone.
    refunds_invoice_id: Optional[UUID] = None
    # Payment month for labor invoices — optional, first-of-month, labor type only.
    # NULL for non-labor invoices and for labor invoices where the month is not tracked.
    service_month: Optional[date] = None
    # How this return is settled ('cash' | 'avoir') — only meaningful when type == RETURN.
    # NULL for every other invoice type.
    settled_via: Optional[str] = None
    # Avoir-only self-link: the invoice this return's credit is applied to (a future
    # materials_services/labor/others invoice, never another return or a released_funds
    # release). Only valid when type == RETURN and settled_via == 'avoir'. ON DELETE
    # SET NULL keeps the return standalone if the target invoice is deleted.
    applied_to_invoice_id: Optional[UUID] = None
    # Worker link for labor invoices — optional, labor type only. NULL for
    # non-labor invoices and for labor invoices with no worker recorded.
    # When set, recipient_name is server-snapshotted from the worker's
    # display name (see CreateInvoiceUseCase / UpdateInvoiceUseCase).
    worker_id: Optional[UUID] = None
    # Company cash advance — released_funds type only. True when this release records
    # money the company handed to a person (typically cash) to pay site expenses on
    # its behalf: an internal transfer, not client money, so it is excluded from the
    # funds_released totals and surfaced as the company purse's cash advanced.
    is_cash_advance: bool = False
    # Optional row-highlight color — one of HIGHLIGHT_COLORS or NULL (no highlight).
    # Applies to invoices of every type; a purely visual annotation with no financial
    # meaning. Validated against the palette at the use-case layer.
    highlight_color: Optional[str] = None

    @property
    def total_amount(self) -> Decimal:
        return sum((item.total for item in self.items), Decimal("0"))

    def with_updates(self, **kwargs: object) -> "Invoice":
        """Return a new Invoice with the given fields replaced.

        Only the supplied keyword arguments are changed; all others carry over.
        Use ``_UNSET`` sentinel to distinguish "not provided" from explicit None.
        """
        return dataclasses.replace(self, **kwargs)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Invoice):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
