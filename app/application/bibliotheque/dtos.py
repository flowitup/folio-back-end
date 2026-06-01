"""Data Transfer Objects for bibliotheque use-case results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.domain.entities.library_product import LibraryProduct
from app.domain.entities.supplier import Supplier
from app.domain.value_objects.library_purchase import LibraryPurchase, SourceDocumentType


@dataclass(frozen=True)
class SupplierResponse:
    """Read model for a supplier."""

    id: UUID
    company_id: UUID
    name: str
    slug: str
    website_url: Optional[str]
    logo_url: Optional[str]
    product_url_template: Optional[str]
    created_at: datetime

    @classmethod
    def from_entity(cls, s: Supplier) -> "SupplierResponse":
        return cls(
            id=s.id,
            company_id=s.company_id,
            name=s.name,
            slug=s.slug,
            website_url=s.website_url,
            logo_url=s.logo_url,
            product_url_template=s.product_url_template,
            created_at=s.created_at,
        )


@dataclass(frozen=True)
class LibraryProductResponse:
    """Read model for a library product (flat — no nested purchases)."""

    id: UUID
    company_id: UUID
    supplier_id: UUID
    supplier_reference: str
    name: str
    description: Optional[str]
    size: Optional[str]
    category: Optional[str]
    has_image: bool
    product_url: Optional[str]
    purchase_count: int
    total_quantity: Decimal
    last_unit_price: Optional[Decimal]
    first_purchased_at: Optional[datetime]
    last_purchased_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, p: LibraryProduct) -> "LibraryProductResponse":
        return cls(
            id=p.id,
            company_id=p.company_id,
            supplier_id=p.supplier_id,
            supplier_reference=p.supplier_reference,
            name=p.name,
            description=p.description,
            size=p.size,
            category=p.category,
            has_image=p.image_storage_key is not None,
            product_url=p.product_url,
            purchase_count=p.purchase_count,
            total_quantity=p.total_quantity,
            last_unit_price=p.last_unit_price,
            first_purchased_at=p.first_purchased_at,
            last_purchased_at=p.last_purchased_at,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


@dataclass(frozen=True)
class LibraryPurchaseResponse:
    """Read model for a single purchase record."""

    product_id: UUID
    source_document_ref: str
    source_document_type: SourceDocumentType
    line_index: int
    purchased_at: datetime
    quantity: Decimal
    unit_price: Decimal

    @classmethod
    def from_vo(cls, p: LibraryPurchase) -> "LibraryPurchaseResponse":
        return cls(
            product_id=p.product_id,
            source_document_ref=p.source_document_ref,
            source_document_type=p.source_document_type,
            line_index=p.line_index,
            purchased_at=p.purchased_at,
            quantity=p.quantity,
            unit_price=p.unit_price,
        )


@dataclass(frozen=True)
class ImportResultDTO:
    """Summary returned by ImportPurchasesUseCase."""

    created: int  # new products created
    updated: int  # existing products with aggregate changes
    purchases_added: int  # purchase rows inserted
    skipped: int  # purchase rows already present (idempotent re-import)


@dataclass(frozen=True)
class ImportRecordDTO:
    """One normalized purchase line plus optional product enrichment.

    Image bytes are NOT included here — they are uploaded separately via
    POST /products/<id>/image so the import endpoint stays pure JSON.
    """

    supplier_reference: str
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    purchased_at: datetime
    source_document_ref: str
    source_document_type: SourceDocumentType
    line_index: int
    # optional enrichment — applied only to empty product fields
    size: Optional[str] = None
    category: Optional[str] = None
    product_url: Optional[str] = None
    description: Optional[str] = None
