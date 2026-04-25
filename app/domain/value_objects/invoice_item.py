"""Invoice item value object — a single line item on an invoice."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InvoiceItem:
    """Value object representing a single line item on an invoice."""

    description: str
    quantity: Decimal
    unit_price: Decimal

    @property
    def total(self) -> Decimal:
        return self.quantity * self.unit_price
