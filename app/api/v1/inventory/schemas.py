"""Pydantic v2 request schemas for the inventory API endpoints."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects.inventory import InventoryCategory, InventoryCondition, InventoryLocationType


class CreateWarehouseSchema(BaseModel):
    """Request body for POST /api/v1/inventory/warehouses."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    name: str = Field(min_length=1, max_length=120)
    address: Optional[str] = Field(default=None, max_length=500)


class UpdateWarehouseSchema(BaseModel):
    """Request body for PATCH /api/v1/inventory/warehouses/<id>; absent key = unchanged, null = cleared."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    address: Optional[str] = Field(default=None, max_length=500)


class CreateInventoryItemSchema(BaseModel):
    """Request body for POST /api/v1/inventory/items."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    name: str = Field(min_length=1, max_length=200)
    category: Optional[InventoryCategory] = None
    reference: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    quantity: int = Field(ge=0, strict=True)
    condition: InventoryCondition
    location_type: InventoryLocationType
    warehouse_id: Optional[UUID] = None
    project_id: Optional[UUID] = None


class UpdateInventoryItemSchema(BaseModel):
    """Request body for PATCH /api/v1/inventory/items/<id>; absent key = unchanged, null = cleared."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: Optional[InventoryCategory] = None
    reference: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    quantity: Optional[int] = Field(default=None, ge=0, strict=True)
    condition: Optional[InventoryCondition] = None
    location_type: Optional[InventoryLocationType] = None
    warehouse_id: Optional[UUID] = None
    project_id: Optional[UUID] = None


class InventoryItemsQuerySchema(BaseModel):
    """Query parameters for GET /api/v1/inventory/items (documentation only; parsed by hand)."""

    company_id: Optional[UUID] = None
    location_type: Optional[InventoryLocationType] = None
    warehouse_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    condition: Optional[InventoryCondition] = None
    q: Optional[str] = None


class CompanyQuerySchema(BaseModel):
    """`?company_id=` (documentation only): the caller's primary company when absent."""

    company_id: Optional[UUID] = None
