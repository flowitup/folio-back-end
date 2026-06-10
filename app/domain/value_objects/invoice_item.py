"""Invoice item value object — a single line item on an invoice."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InvoiceItem:
    """Value object representing a single line item on an invoice.

    vat_rate is a percentage (e.g. 20 for 20%). Must be in [0, 100].
    total returns TTC (HT + TVA); total_ht is the pre-tax subtotal.
    """

    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.vat_rate <= Decimal("100")):
            raise ValueError(f"vat_rate must be between 0 and 100, got {self.vat_rate}")

    @property
    def total_ht(self) -> Decimal:
        """Pre-tax line total: quantity × unit_price."""
        return self.quantity * self.unit_price

    @property
    def total_tva(self) -> Decimal:
        """VAT amount: total_ht × vat_rate / 100."""
        return self.total_ht * self.vat_rate / Decimal("100")

    @property
    def total(self) -> Decimal:
        """TTC line total: total_ht + total_tva."""
        return self.total_ht + self.total_tva
