"""Request line items → application `ItemInput`, shared by the document and template routes.

One helper for both so a line field (such as the section `category`) cannot
be carried by one route and silently dropped by the other.
"""

from __future__ import annotations

from app.application.billing import ItemInput


def items_from_schema(raw_items) -> list[ItemInput]:
    return [
        ItemInput(
            description=it.description,
            quantity=it.quantity,
            unit_price=it.unit_price,
            vat_rate=it.vat_rate,
            category=getattr(it, "category", None),
        )
        for it in raw_items
    ]
