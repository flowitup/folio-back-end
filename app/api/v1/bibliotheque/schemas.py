"""Pydantic v2 request schemas for the bibliotheque API endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class ImportRecordSchema(BaseModel):
    """One purchase line plus optional product enrichment fields."""

    model_config = ConfigDict(extra="forbid")

    supplier_reference: str = Field(min_length=1, max_length=200)
    product_name: str = Field(min_length=1, max_length=1000)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    purchased_at: datetime
    source_document_ref: str = Field(min_length=1, max_length=255)
    source_document_type: Literal["ticket", "commande"]
    line_index: int = Field(ge=0)
    # Optional enrichment
    size: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=200)
    product_url: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=1000)


class ImportRequestSchema(BaseModel):
    """Request body for POST /api/v1/bibliotheque/import."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    supplier_name: str = Field(min_length=1, max_length=255)
    supplier_slug: str = Field(min_length=1, max_length=100)
    supplier_website_url: Optional[str] = Field(default=None, max_length=500)
    supplier_product_url_template: Optional[str] = Field(default=None, max_length=500)
    records: List[ImportRecordSchema] = Field(min_length=1, max_length=1000)

    @field_validator("records")
    @classmethod
    def validate_records_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("records must not be empty")
        return v


class RecategorizeItemSchema(BaseModel):
    """One (supplier_reference -> category) reassignment."""

    model_config = ConfigDict(extra="forbid")

    supplier_reference: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=200)


class RecategorizeRequestSchema(BaseModel):
    """Request body for POST /api/v1/bibliotheque/recategorize."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    supplier_slug: str = Field(min_length=1, max_length=100)
    items: List[RecategorizeItemSchema] = Field(min_length=1, max_length=10000)


class ImageFromUrlSchema(BaseModel):
    """Request body for POST /api/v1/bibliotheque/products/<id>/image-from-url."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(description="HTTPS URL of the product image to fetch server-side.")
