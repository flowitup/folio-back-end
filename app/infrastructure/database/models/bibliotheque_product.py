"""SQLAlchemy ORM model for bibliotheque_products table."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.library_product import LibraryProduct
from app.infrastructure.database.models.base import Base


class BibliothequeProductModel(Base):
    """SQLAlchemy mapping for bibliotheque_products.

    Unique on (company_id, supplier_id, supplier_reference) — one product row
    per supplier-reference per company. Aggregates (purchase_count, total_quantity,
    last_unit_price, first/last purchased_at) are denormalized here and kept
    consistent by the import use-case via with_purchase_applied + upsert.
    """

    __tablename__ = "bibliotheque_products"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    supplier_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bibliotheque_suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    supplier_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    size: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Nullable unique: PostgreSQL permits multiple NULLs in a unique index — correct behaviour.
    image_storage_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, unique=True)
    product_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    purchase_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_quantity: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=4), nullable=False, default=Decimal("0")
    )
    last_unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=18, scale=4), nullable=True)
    first_purchased_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_purchased_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "supplier_id",
            "supplier_reference",
            name="uq_bibliotheque_products_company_supplier_ref",
        ),
        Index("ix_bibliotheque_products_company_supplier", "company_id", "supplier_id"),
        Index("ix_bibliotheque_products_company_category", "company_id", "category"),
    )

    def to_entity(self) -> LibraryProduct:
        return LibraryProduct(
            id=self.id,
            company_id=self.company_id,
            supplier_id=self.supplier_id,
            supplier_reference=self.supplier_reference,
            name=self.name,
            description=self.description,
            size=self.size,
            category=self.category,
            image_storage_key=self.image_storage_key,
            product_url=self.product_url,
            purchase_count=self.purchase_count,
            total_quantity=(
                self.total_quantity if isinstance(self.total_quantity, Decimal) else Decimal(str(self.total_quantity))
            ),
            last_unit_price=Decimal(str(self.last_unit_price)) if self.last_unit_price is not None else None,
            first_purchased_at=self.first_purchased_at,
            last_purchased_at=self.last_purchased_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, p: LibraryProduct) -> "BibliothequeProductModel":
        return cls(
            id=p.id,
            company_id=p.company_id,
            supplier_id=p.supplier_id,
            supplier_reference=p.supplier_reference,
            name=p.name,
            description=p.description,
            size=p.size,
            category=p.category,
            image_storage_key=p.image_storage_key,
            product_url=p.product_url,
            purchase_count=p.purchase_count,
            total_quantity=p.total_quantity,
            last_unit_price=p.last_unit_price,
            first_purchased_at=p.first_purchased_at,
            last_purchased_at=p.last_purchased_at,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )

    def update_from_entity(self, p: LibraryProduct) -> None:
        """Mutate this ORM row in-place for save/upsert operations."""
        self.name = p.name
        self.description = p.description
        self.size = p.size
        self.category = p.category
        self.image_storage_key = p.image_storage_key
        self.product_url = p.product_url
        self.purchase_count = p.purchase_count
        self.total_quantity = p.total_quantity
        self.last_unit_price = p.last_unit_price
        self.first_purchased_at = p.first_purchased_at
        self.last_purchased_at = p.last_purchased_at
        self.updated_at = p.updated_at

    def __repr__(self) -> str:
        return f"<BibliothequeProductModel {self.id} ref='{self.supplier_reference}'>"
