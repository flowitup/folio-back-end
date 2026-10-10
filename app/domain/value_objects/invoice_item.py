"""Invoice item value object — a single line item on an invoice."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

_CENT = Decimal("0.01")


def round_to_cents(value: Decimal) -> Decimal:
    """Round a money amount half-up to the cent (0.125 → 0.13).

    The precision is widened to the value's size: at the default 28 digits,
    quantize() raises on a larger amount instead of rounding it.
    """
    if not value.is_finite():
        return value
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, value.adjusted() + 3)
        return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class InvoiceItem:
    """Value object representing a single line item on an invoice.

    vat_rate is a percentage (e.g. 20 for 20%). Must be in [0, 100].
    total returns TTC (HT + TVA) rounded to the cent; total_ht is the exact
    pre-tax subtotal.
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
        """TTC line total: total_ht + total_tva, rounded half-up to the cent.

        A line is shown at the cent everywhere (ledger, print page, exports), so
        rounding it here makes an invoice's total — the sum of its lines — equal
        the sum of the lines it shows.
        """
        return round_to_cents(self.total_ht + self.total_tva)
