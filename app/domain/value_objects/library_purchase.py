"""LibraryPurchase value object — one line of a purchase record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

SourceDocumentType = Literal["ticket", "commande"]


@dataclass(frozen=True)
class LibraryPurchase:
    """Value object representing a single purchase line linked to a product.

    The triple (product_id, source_document_ref, line_index) is the idempotency
    key: re-importing the same purchase line must produce exactly one row.
    """

    product_id: UUID
    source_document_ref: str
    source_document_type: SourceDocumentType
    line_index: int
    purchased_at: datetime
    quantity: Decimal
    unit_price: Decimal
