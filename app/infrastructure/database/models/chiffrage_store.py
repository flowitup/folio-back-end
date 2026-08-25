"""SQLAlchemy ORM model for the chiffrage_stores table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_store import ChiffrageStore
from app.infrastructure.database.models.base import Base


class ChiffrageStoreModel(Base):
    """SQLAlchemy mapping for chiffrage_stores — shops the project buys from.

    Unique on (project_id, lower(name)) so a shop cannot be entered twice under
    the same name: two spellings would split one shop's basket in the
    comparison, which is precisely the failure this table exists to prevent.
    """

    __tablename__ = "chiffrage_stores"
    __table_args__ = (
        Index("ix_chiffrage_stores_project_position", "project_id", "position"),
        Index(
            "uq_chiffrage_stores_project_name",
            "project_id",
            func.lower(text("name")),
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
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
            project_id=self.project_id,
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
            project_id=s.project_id,
            name=s.name,
            address=s.address,
            website_url=s.website_url,
            position=s.position,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    def __repr__(self) -> str:
        return f"<ChiffrageStoreModel {self.id} '{self.name}' project={self.project_id}>"
