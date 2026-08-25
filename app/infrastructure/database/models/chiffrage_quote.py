"""SQLAlchemy ORM model for the chiffrage_quotes table."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_quote import ChiffrageQuote
from app.infrastructure.database.models.base import Base


class ChiffrageQuoteModel(Base):
    """SQLAlchemy mapping for chiffrage_quotes — one fournisseur offer.

    The retained offer is flagged in-row via ``is_selected`` rather than pointed
    at by the article. A selected_quote_id on the article would be a circular
    foreign key (breaking SQLite create_all in tests) and would strand a dangling
    pointer whenever the quote is deleted.

    ``store_id`` is what makes a price comparable: it points at one of the
    project's shops, so every quote at that shop aggregates into one basket.
    It is ON DELETE SET NULL like the bibliothèque references — deleting a shop
    must not delete the prices recorded there — and the free-text
    ``supplier_name`` survives as a readable snapshot, so removing a shop,
    supplier or product never blanks out a costing row.
    """

    __tablename__ = "chiffrage_quotes"
    __table_args__ = (
        CheckConstraint(
            "store_id IS NOT NULL OR supplier_id IS NOT NULL OR supplier_name IS NOT NULL",
            name="ck_chiffrage_quotes_supplier_present",
        ),
        # Composite covers both "all quotes of an article" and the selection
        # lookup; a bare index on article_id alone would be redundant with this.
        Index("ix_chiffrage_quotes_article_selected", "article_id", "is_selected"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    article_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chiffrage_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chiffrage_stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    supplier_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bibliotheque_suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    supplier_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    library_product_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bibliotheque_products.id", ondelete="SET NULL"),
        nullable=True,
    )
    unit_price_ht: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    tva_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("20"))
    product_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_entity(self) -> ChiffrageQuote:
        return ChiffrageQuote(
            id=self.id,
            article_id=self.article_id,
            store_id=self.store_id,
            supplier_id=self.supplier_id,
            supplier_name=self.supplier_name,
            library_product_id=self.library_product_id,
            unit_price_ht=Decimal(str(self.unit_price_ht)),
            tva_rate=Decimal(str(self.tva_rate)),
            product_url=self.product_url,
            note=self.note,
            is_selected=self.is_selected,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, q: ChiffrageQuote) -> "ChiffrageQuoteModel":
        return cls(
            id=q.id,
            article_id=q.article_id,
            store_id=q.store_id,
            supplier_id=q.supplier_id,
            supplier_name=q.supplier_name,
            library_product_id=q.library_product_id,
            unit_price_ht=q.unit_price_ht,
            tva_rate=q.tva_rate,
            product_url=q.product_url,
            note=q.note,
            is_selected=q.is_selected,
            created_at=q.created_at,
            updated_at=q.updated_at,
        )

    def __repr__(self) -> str:
        return f"<ChiffrageQuoteModel {self.id} article={self.article_id} ht={self.unit_price_ht}>"
