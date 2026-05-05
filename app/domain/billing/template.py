"""BillingDocumentTemplate domain entity for the billing bounded context.

Templates are user-curated skeletons. They store structure (items, notes, terms,
default VAT rate) but never recipient data, dates, status, or document numbers.
Applying a template pre-fills a new BillingDocument with these fields.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.value_objects import BillingDocumentItem


@dataclass(frozen=True, slots=True)
class BillingDocumentTemplate:
    """Immutable template entity.

    items is a tuple of BillingDocumentItem value objects (frozen, order-preserving).
    default_vat_rate is stored as a Decimal percent (e.g. Decimal("20")) or None.
    """

    id: UUID
    user_id: UUID
    kind: BillingDocumentKind
    name: str
    created_at: datetime
    updated_at: datetime
    notes: Optional[str] = None
    terms: Optional[str] = None
    default_vat_rate: Optional[Decimal] = None
    items: tuple[BillingDocumentItem, ...] = field(default_factory=tuple)

    def with_updates(self, **kwargs: object) -> "BillingDocumentTemplate":
        """Return a new BillingDocumentTemplate with the given fields replaced."""
        return dataclasses.replace(self, **kwargs)
