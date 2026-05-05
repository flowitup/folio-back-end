"""Pure functions for computing billing document totals.

compute_totals   — aggregate BillingDocumentItem list into DocumentTotals.
vat_breakdown    — return (rate, base_ht, tva_amount) tuples sorted by rate descending.

All arithmetic in Decimal; no quantization here (serialisation layer quantizes).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from app.domain.billing.value_objects import BillingDocumentItem, DocumentTotals


def compute_totals(items: Iterable[BillingDocumentItem]) -> DocumentTotals:
    """Aggregate a collection of BillingDocumentItem into DocumentTotals.

    VAT rates are grouped by their normalized form so that Decimal("20"),
    Decimal("20.0"), and Decimal("20.00") all map to the same bucket.
    """
    items_list = list(items)

    total_ht = Decimal("0")
    tva_by_rate: dict[Decimal, Decimal] = {}

    for item in items_list:
        total_ht += item.total_ht
        rate_key = item.vat_rate.normalize()
        tva_by_rate[rate_key] = tva_by_rate.get(rate_key, Decimal("0")) + item.total_tva

    total_tva = sum(tva_by_rate.values(), Decimal("0"))
    total_ttc = total_ht + total_tva

    return DocumentTotals(
        total_ht=total_ht,
        total_tva_by_rate=tva_by_rate,
        total_tva=total_tva,
        total_ttc=total_ttc,
    )


def vat_breakdown(
    items: Iterable[BillingDocumentItem],
) -> list[tuple[Decimal, Decimal, Decimal]]:
    """Return a list of (rate, base_ht, tva_amount) tuples, sorted by rate descending.

    Useful for rendering the VAT breakdown block in PDFs and UI totals panels.
    Each tuple:
        rate       — normalized VAT rate (e.g. Decimal("20"))
        base_ht    — sum of total_ht for all lines at that rate
        tva_amount — sum of total_tva for all lines at that rate
    """
    items_list = list(items)

    base_by_rate: dict[Decimal, Decimal] = {}
    tva_by_rate: dict[Decimal, Decimal] = {}

    for item in items_list:
        rate_key = item.vat_rate.normalize()
        base_by_rate[rate_key] = base_by_rate.get(rate_key, Decimal("0")) + item.total_ht
        tva_by_rate[rate_key] = tva_by_rate.get(rate_key, Decimal("0")) + item.total_tva

    return sorted(
        [(rate, base_by_rate[rate], tva_by_rate[rate]) for rate in base_by_rate],
        key=lambda t: t[0],
        reverse=True,
    )
