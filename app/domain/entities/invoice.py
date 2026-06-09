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


class RefundableStatus(str, Enum):
    """Lifecycle states for company-scoped refund tracking on materials & services expenses."""

    REFUNDABLE = "refundable"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"


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
