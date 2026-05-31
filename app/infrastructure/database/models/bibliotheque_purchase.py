"""SQLAlchemy ORM model for bibliotheque_purchases table."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.value_objects.library_purchase import LibraryPurchase
from app.infrastructure.database.models.base import Base


class BibliothequePurchaseModel(Base):
    """SQLAlchemy mapping for bibliotheque_purchases.

    Unique on (product_id, source_document_ref, line_index) — the idempotency
    key for re-import. Attempting to insert a duplicate row is the repository's
    signal to skip aggregate updates on the parent product.
    """

    __tablename__ = "bibliotheque_purchases"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bibliotheque_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_document_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "source_document_ref",
            "line_index",
            name="uq_bibliotheque_purchases_idempotency_key",
        ),
        Index("ix_bibliotheque_purchases_product_purchased_at", "product_id", "purchased_at"),
    )

    def to_vo(self) -> LibraryPurchase:
        return LibraryPurchase(
            product_id=self.product_id,
            source_document_ref=self.source_document_ref,
            source_document_type=self.source_document_type,  # type: ignore[arg-type]
            line_index=self.line_index,
            purchased_at=self.purchased_at,
            quantity=self.quantity if isinstance(self.quantity, Decimal) else Decimal(str(self.quantity)),
            unit_price=self.unit_price if isinstance(self.unit_price, Decimal) else Decimal(str(self.unit_price)),
        )

    @classmethod
    def from_vo(cls, p: LibraryPurchase) -> "BibliothequePurchaseModel":
        return cls(
            id=uuid4(),
            product_id=p.product_id,
            source_document_ref=p.source_document_ref,
            source_document_type=p.source_document_type,
            line_index=p.line_index,
            purchased_at=p.purchased_at,
            quantity=p.quantity,
            unit_price=p.unit_price,
        )

    def __repr__(self) -> str:
        return (
            f"<BibliothequePurchaseModel product={self.product_id} "
            f"ref='{self.source_document_ref}' line={self.line_index}>"
        )
