"""SQLAlchemy ORM model for the chiffrage_stores table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_store import ChiffrageStore
from app.infrastructure.database.models.base import Base


class ChiffrageStoreModel(Base):
    """SQLAlchemy mapping for chiffrage_stores — shops to visit for a poste."""

    __tablename__ = "chiffrage_stores"
    __table_args__ = (Index("ix_chiffrage_stores_poste_position", "poste_id", "position"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    poste_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chiffrage_postes.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_entity(self) -> ChiffrageStore:
        return ChiffrageStore(
            id=self.id,
            poste_id=self.poste_id,
            name=self.name,
            address=self.address,
            website_url=self.website_url,
            position=self.position,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, s: ChiffrageStore) -> "ChiffrageStoreModel":
        return cls(
            id=s.id,
            poste_id=s.poste_id,
            name=s.name,
            address=s.address,
            website_url=s.website_url,
            position=s.position,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    def __repr__(self) -> str:
        return f"<ChiffrageStoreModel {self.id} '{self.name}' poste={self.poste_id}>"
