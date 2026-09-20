"""Read models returned by the inventory use-cases (serialised with dataclasses.asdict)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.domain.entities.inventory_item import InventoryItem
from app.domain.entities.warehouse import Warehouse


@dataclass(frozen=True)
class WarehouseResponse:
    id: UUID
    company_id: UUID
    name: str
    address: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, w: Warehouse) -> "WarehouseResponse":
        return cls(
            id=w.id,
            company_id=w.company_id,
            name=w.name,
            address=w.address,
            created_at=w.created_at,
            updated_at=w.updated_at,
        )


@dataclass(frozen=True)
class InventoryItemResponse:
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
    def from_entity(cls, i: InventoryItem) -> "InventoryItemResponse":
        return cls(
            id=i.id,
            company_id=i.company_id,
            name=i.name,
            category=i.category,
            reference=i.reference,
            description=i.description,
            quantity=i.quantity,
            condition=i.condition,
            location_type=i.location_type,
            warehouse_id=i.warehouse_id,
            project_id=i.project_id,
            created_at=i.created_at,
            updated_at=i.updated_at,
        )
