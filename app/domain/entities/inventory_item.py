"""InventoryItem domain entity — one batch of identical equipment in one place and one condition.

A tool that is partly broken, or split between the warehouse and a site, is
several rows: three working drills at the warehouse and one broken drill on a
site are two rows. Quantities are whole units and never negative.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.domain.value_objects.inventory import (
    INVENTORY_CONDITIONS,
    INVENTORY_LOCATION_TYPES,
    is_valid_inventory_category,
)


class InvalidInventoryItemError(ValueError):
    """Raised when a row would violate an invariant (bad enum, negative quantity, place without id)."""


@dataclass(frozen=True)
class InventoryItem:
    id: UUID
    company_id: UUID
    name: str
    category: Optional[str]
    reference: Optional[str]
    description: Optional[str]
    quantity: int
    condition: str
    location_type: str
    warehouse_id: Optional[UUID]
    project_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        company_id: UUID,
        name: str,
        quantity: int,
        condition: str,
        location_type: str,
        warehouse_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        category: Optional[str] = None,
        reference: Optional[str] = None,
        description: Optional[str] = None,
    ) -> "InventoryItem":
        now = datetime.now(timezone.utc)
        item = cls(
            id=uuid4(),
            company_id=company_id,
            name=name.strip(),
            category=_clean(category),
            reference=_clean(reference),
            description=_clean(description),
            quantity=quantity,
            condition=condition,
            location_type=location_type,
            warehouse_id=warehouse_id,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )
        item.validate()
        return item

    def validate(self) -> None:
        """Raise InvalidInventoryItemError unless every invariant holds."""
        if not self.name:
            raise InvalidInventoryItemError("name is required.")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity < 0:
            raise InvalidInventoryItemError("quantity must be a whole number, 0 or more.")
        if self.condition not in INVENTORY_CONDITIONS:
            raise InvalidInventoryItemError(f"condition must be one of {sorted(INVENTORY_CONDITIONS)}.")
        if self.location_type not in INVENTORY_LOCATION_TYPES:
            raise InvalidInventoryItemError(f"location_type must be one of {sorted(INVENTORY_LOCATION_TYPES)}.")
        if self.category is not None and not is_valid_inventory_category(self.category):
            raise InvalidInventoryItemError(f"Invalid category slug {self.category!r}.")
        if self.location_type == "warehouse":
            if self.warehouse_id is None:
                raise InvalidInventoryItemError("warehouse_id is required when location_type is 'warehouse'.")
            if self.project_id is not None:
                raise InvalidInventoryItemError("project_id must be null when location_type is 'warehouse'.")
        else:
            if self.project_id is None:
                raise InvalidInventoryItemError("project_id is required when location_type is 'site'.")
            if self.warehouse_id is not None:
                raise InvalidInventoryItemError("warehouse_id must be null when location_type is 'site'.")

    # Sentinel so callers can distinguish "leave field unchanged" from an explicit clear.
    _UNSET = object()

    def with_updates(
        self,
        *,
        name: object = _UNSET,
        category: object = _UNSET,
        reference: object = _UNSET,
        description: object = _UNSET,
        quantity: object = _UNSET,
        condition: object = _UNSET,
        location_type: object = _UNSET,
        warehouse_id: object = _UNSET,
        project_id: object = _UNSET,
    ) -> "InventoryItem":
        """Return a validated copy with the given fields overwritten; `_UNSET` fields are kept.

        A location change clears the counterpart id on its own when the caller
        only sends the new kind and its id: moving to a site with `project_id`
        set drops `warehouse_id`, and the other way round.
        """
        u = InventoryItem._UNSET
        next_location = self.location_type if location_type is u else str(location_type)
        next_warehouse = self.warehouse_id if warehouse_id is u else warehouse_id
        next_project = self.project_id if project_id is u else project_id
        if next_location != self.location_type:
            if next_location == "warehouse" and project_id is u:
                next_project = None
            if next_location == "site" and warehouse_id is u:
                next_warehouse = None
        updated = replace(
            self,
            name=self.name if name is u else str(name).strip(),
            category=self.category if category is u else _clean(category),  # type: ignore[arg-type]
            reference=self.reference if reference is u else _clean(reference),  # type: ignore[arg-type]
            description=self.description if description is u else _clean(description),  # type: ignore[arg-type]
            quantity=self.quantity if quantity is u else quantity,  # type: ignore[arg-type]
            condition=self.condition if condition is u else str(condition),
            location_type=next_location,
            warehouse_id=next_warehouse,  # type: ignore[arg-type]
            project_id=next_project,  # type: ignore[arg-type]
        )
        if updated == self:
            return self
        updated = replace(updated, updated_at=datetime.now(timezone.utc))
        updated.validate()
        return updated


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
