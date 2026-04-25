"""Invoice domain entity."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID


class InvoiceType(str, Enum):
    CLIENT = "client"
    LABOR = "labor"
    SUPPLIER = "supplier"


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

    @property
    def total_amount(self) -> Decimal:
        return sum((item.total for item in self.items), Decimal("0"))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Invoice):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
