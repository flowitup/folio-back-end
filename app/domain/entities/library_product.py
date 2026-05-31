"""LibraryProduct domain entity — a product in the company product library."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4


@dataclass(frozen=True)
class LibraryProduct:
    """Immutable product entity for the bibliotheque bounded context.

    Aggregates are maintained in-entity: when a purchase is inserted
    (idempotency enforced at the repository layer), the caller must call
    with_purchase_applied so counts/prices/dates stay consistent without
    a separate aggregate-update query.
    """

    id: UUID
    company_id: UUID
    supplier_id: UUID
    supplier_reference: str
    name: str
    description: Optional[str]
    size: Optional[str]
    category: Optional[str]
    image_storage_key: Optional[str]
    product_url: Optional[str]
    purchase_count: int
    total_quantity: Decimal
    last_unit_price: Optional[Decimal]
    first_purchased_at: Optional[datetime]
    last_purchased_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        company_id: UUID,
        supplier_id: UUID,
        supplier_reference: str,
        name: str,
        description: Optional[str] = None,
        size: Optional[str] = None,
        category: Optional[str] = None,
        image_storage_key: Optional[str] = None,
        product_url: Optional[str] = None,
    ) -> "LibraryProduct":
        """Create a new product with zero-aggregate initial state."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference=supplier_reference,
            name=name,
            description=description,
            size=size,
            category=category,
            image_storage_key=image_storage_key,
            product_url=product_url,
            purchase_count=0,
            total_quantity=Decimal("0"),
            last_unit_price=None,
            first_purchased_at=None,
            last_purchased_at=None,
            created_at=now,
            updated_at=now,
        )

    def with_purchase_applied(
        self,
        qty: Decimal,
        unit_price: Decimal,
        purchased_at: datetime,
    ) -> "LibraryProduct":
        """Return a copy with purchase aggregates updated.

        Called ONLY when a purchase was actually inserted (add_if_absent returned
        True). Calling this on a duplicate import would double-count; idempotency
        is the repository's responsibility, not this method's.
        """
        new_first = self.first_purchased_at if self.first_purchased_at else purchased_at
        new_last = (
            purchased_at
            if (self.last_purchased_at is None or purchased_at > self.last_purchased_at)
            else self.last_purchased_at
        )
        return replace(
            self,
            purchase_count=self.purchase_count + 1,
            total_quantity=self.total_quantity + qty,
            last_unit_price=unit_price,
            first_purchased_at=new_first,
            last_purchased_at=new_last,
            updated_at=datetime.now(timezone.utc),
        )

    def with_enrichment(
        self,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        size: Optional[str] = None,
        category: Optional[str] = None,
        image_storage_key: Optional[str] = None,
        product_url: Optional[str] = None,
    ) -> "LibraryProduct":
        """Return a copy with enrichment fields applied to empty slots only.

        Never overwrites an existing non-null field with a null value.
        This allows re-importing records with partial enrichment without
        clobbering manually curated data.
        """
        return replace(
            self,
            name=name if name is not None else self.name,
            description=description if description is not None and self.description is None else self.description,
            size=size if size is not None and self.size is None else self.size,
            category=category if category is not None and self.category is None else self.category,
            image_storage_key=(
                image_storage_key
                if image_storage_key is not None and self.image_storage_key is None
                else self.image_storage_key
            ),
            product_url=product_url if product_url is not None and self.product_url is None else self.product_url,
            updated_at=datetime.now(timezone.utc),
        )
