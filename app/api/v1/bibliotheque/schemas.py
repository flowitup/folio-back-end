"""Pydantic v2 request schemas for the bibliotheque API endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.domain.value_objects.library_category import is_valid_category_slug


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


class UpdateProductSchema(BaseModel):
    """Request body for PATCH /api/v1/bibliotheque/products/<id>.

    All fields optional — only fields present in the payload are updated
    (use model_fields_set / exclude_unset to distinguish omitted from null).
    Sending an explicit null clears the field. Image is edited via the
    dedicated image endpoints, not here.

    category must be one of the 16 canonical slugs (see library_category.py)
    or null to clear. Free-text is NOT accepted here; use the import endpoint
    which normalises free-text automatically via normalize_category().
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=500)
    category: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    size: Optional[str] = Field(default=None, max_length=100)
    product_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("product_url", mode="before")
    @classmethod
    def _http_scheme_only(cls, v: object) -> object:
        """Reject non-null URL values that do not start with http:// or https://."""
        if v and not str(v).lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @field_validator("category", mode="before")
    @classmethod
    def validate_category_slug(cls, v: object) -> object:
        """Reject non-null category values that are not valid canonical slugs."""
        if v is not None and not is_valid_category_slug(str(v)):
            raise ValueError(
                f"Invalid category slug {v!r}. Must be one of the canonical slugs "
                "(e.g. 'plomberie', 'outillage', 'autre'). "
                "Use the import endpoint for free-text normalisation."
            )
        return v


class CreateProductSchema(BaseModel):
    """Request body for POST /api/v1/bibliotheque/products.

    Exactly one of supplier_id or supplier_name must be provided.
    supplier_website_url is only meaningful when supplier_name is given.
    category must be a canonical slug or null (same constraint as UpdateProductSchema).
    """

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    name: str = Field(min_length=1, max_length=500)
    supplier_id: Optional[UUID] = None
    supplier_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    supplier_website_url: Optional[str] = Field(default=None, max_length=500)
    supplier_reference: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    size: Optional[str] = Field(default=None, max_length=100)
    product_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("product_url", "supplier_website_url", mode="before")
    @classmethod
    def _http_scheme_only(cls, v: object) -> object:
        """Reject non-null URL values that do not start with http:// or https://."""
        if v and not str(v).lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @model_validator(mode="after")
    def _exactly_one_supplier(self) -> "CreateProductSchema":
        """Enforce that exactly one of supplier_id or supplier_name is provided."""
        if bool(self.supplier_id) == bool(self.supplier_name):
            raise ValueError("Provide exactly one of supplier_id or supplier_name.")
        return self

    @field_validator("category", mode="before")
    @classmethod
    def validate_category_slug(cls, v: object) -> object:
        """Reject non-null category values that are not valid canonical slugs."""
        if v is not None and not is_valid_category_slug(str(v)):
            raise ValueError(
                f"Invalid category slug {v!r}. Must be one of the canonical slugs "
                "(e.g. 'plomberie', 'outillage', 'autre'). "
                "Use the import endpoint for free-text normalisation."
            )
        return v


class ImageFromUrlSchema(BaseModel):
    """Request body for POST /api/v1/bibliotheque/products/<id>/image-from-url."""

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(description="HTTPS URL of the product image to fetch server-side.")
