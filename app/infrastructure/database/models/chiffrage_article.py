"""SQLAlchemy ORM model for the chiffrage_articles table."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_article import ChiffrageArticle
from app.infrastructure.database.models.base import Base


class ChiffrageArticleModel(Base):
    """SQLAlchemy mapping for chiffrage_articles — one thing to buy in a poste.

    ``unit`` is a snapshot symbol, deliberately NOT a foreign key to
    chiffrage_units: deleting a custom unit must never break articles that
    already reference it. The allowed set is enforced at the API boundary.
    """

    __tablename__ = "chiffrage_articles"
    __table_args__ = (Index("ix_chiffrage_articles_poste_position", "poste_id", "position"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    poste_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chiffrage_postes.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=Decimal("0"))
    unit: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_entity(self) -> ChiffrageArticle:
        return ChiffrageArticle(
            id=self.id,
            poste_id=self.poste_id,
            name=self.name,
            quantity=Decimal(str(self.quantity)),
            unit=self.unit,
            note=self.note,
            position=self.position,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, a: ChiffrageArticle) -> "ChiffrageArticleModel":
        return cls(
            id=a.id,
            poste_id=a.poste_id,
            name=a.name,
            quantity=a.quantity,
            unit=a.unit,
            note=a.note,
            position=a.position,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )

    def __repr__(self) -> str:
        return f"<ChiffrageArticleModel {self.id} '{self.name}' poste={self.poste_id}>"
