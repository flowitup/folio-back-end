"""SQLAlchemy ORM model for the chiffrage_postes table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_poste import ChiffragePoste
from app.infrastructure.database.models.base import Base


class ChiffragePosteModel(Base):
    """SQLAlchemy mapping for chiffrage_postes — a costing section of a project."""

    __tablename__ = "chiffrage_postes"
    __table_args__ = (
        # Serves the tree read: every poste of a project, in display order.
        Index("ix_chiffrage_postes_project_position", "project_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_entity(self) -> ChiffragePoste:
        return ChiffragePoste(
            id=self.id,
            project_id=self.project_id,
            name=self.name,
            note=self.note,
            position=self.position,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, p: ChiffragePoste) -> "ChiffragePosteModel":
        return cls(
            id=p.id,
            project_id=p.project_id,
            name=p.name,
            note=p.note,
            position=p.position,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )

    def __repr__(self) -> str:
        return f"<ChiffragePosteModel {self.id} '{self.name}' project={self.project_id}>"
