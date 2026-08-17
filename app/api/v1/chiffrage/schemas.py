"""Pydantic v2 request schemas for the chiffrage API.

PATCH bodies rely on ``model_fields_set`` rather than None-as-absent so that
clearing a nullable field (note, product_url, supplier link) is expressible and
distinct from omitting it. Routes translate an omitted field into the entity's
_UNSET sentinel.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PosteCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    note: Optional[str] = Field(default=None, max_length=2000)


class PosteUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    note: Optional[str] = Field(default=None, max_length=2000)


class ArticleCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(ge=0)
    unit: Optional[str] = Field(default=None, max_length=16)
    note: Optional[str] = Field(default=None, max_length=2000)


class ArticleUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    quantity: Optional[Decimal] = Field(default=None, ge=0)
    unit: Optional[str] = Field(default=None, max_length=16)
    note: Optional[str] = Field(default=None, max_length=2000)


class QuoteCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price_ht: Decimal = Field(ge=0)
    tva_rate: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    supplier_id: Optional[UUID] = None
    supplier_name: Optional[str] = Field(default=None, max_length=120)
    library_product_id: Optional[UUID] = None
    product_url: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _supplier_present(self) -> "QuoteCreateBody":
        """A quote must name its fournisseur one way or the other."""
        if self.supplier_id is None and not (self.supplier_name or "").strip():
            raise ValueError("Either supplier_id or supplier_name is required.")
        return self


class QuoteUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price_ht: Optional[Decimal] = Field(default=None, ge=0)
    tva_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    supplier_id: Optional[UUID] = None
    supplier_name: Optional[str] = Field(default=None, max_length=120)
    library_product_id: Optional[UUID] = None
    product_url: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = Field(default=None, max_length=2000)


class ReorderBody(BaseModel):
    """Drop target expressed as its neighbours; both absent means append."""

    model_config = ConfigDict(extra="forbid")

    before_id: Optional[UUID] = None
    after_id: Optional[UUID] = None


class UnitCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
