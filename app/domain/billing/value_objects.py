"""Frozen value objects for the billing bounded context.

BillingDocumentItem — a single line item on a billing document.
DocumentTotals      — aggregated totals computed from a collection of items.

Currency math uses Decimal throughout, with one rounding rule shared by the
API, the PDF and the XLSX export: each line's HT is rounded half-up to the cent,
then its TVA is rounded half-up to the cent from that rounded HT. Document
totals are plain sums of those cent amounts, so lines always add up to the
totals and HT + TVA == TTC exactly.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping, Optional


_CENT = Decimal("0.01")


def round_cents(value: Decimal) -> Decimal:
    """Round a money amount half-up to the cent (0.125 → 0.13)."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class BillingDocumentItem:
    """Frozen value object representing a single line item on a billing document.

    vat_rate is a percentage expressed as a Decimal, e.g. Decimal("20") for 20% VAT.
    Line amounts are rounded to the cent (see the module docstring).

    category is an optional free-text section/trade label (max 120 chars, trimmed).
    Empty string is coerced to None on construction via __post_init__.
    """

    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal  # percent, e.g. Decimal("20")
    category: Optional[str] = None

    def __post_init__(self) -> None:
        if self.category is not None:
            stripped = self.category.strip()
            if not stripped:
                # empty/whitespace-only → None (bypass frozen by using object.__setattr__)
                object.__setattr__(self, "category", None)
            elif len(stripped) > 120:
                raise ValueError("category exceeds 120 characters")
            else:
                object.__setattr__(self, "category", stripped)

    @property
    def total_ht(self) -> Decimal:
        """Line total before VAT (quantity × unit_price), rounded to the cent."""
        return round_cents(self.quantity * self.unit_price)

    @property
    def total_tva(self) -> Decimal:
        """VAT amount for this line (total_ht × vat_rate / 100), rounded to the cent."""
        return round_cents(self.total_ht * self.vat_rate / Decimal("100"))

    @property
    def total_ttc(self) -> Decimal:
        """Line total including VAT."""
        return self.total_ht + self.total_tva


@dataclass(frozen=True, slots=True)
class DocumentTotals:
    """Aggregated totals computed from a collection of BillingDocumentItem instances.

    total_tva_by_rate maps normalized VAT rate keys to VAT amounts,
    e.g. {Decimal("20"): Decimal("40"), Decimal("10"): Decimal("5")}.
    Keys are normalized via Decimal.normalize() so 20 == 20.0 == 20.00.
    """

    total_ht: Decimal
    total_tva_by_rate: Mapping[Decimal, Decimal]
    total_tva: Decimal
    total_ttc: Decimal
