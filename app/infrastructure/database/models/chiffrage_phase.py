"""SQLAlchemy ORM model for the chiffrage_phases table."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_phase import ChiffragePhase
from app.infrastructure.database.models.base import Base


class ChiffragePhaseModel(Base):
    """SQLAlchemy mapping for chiffrage_phases — a poste's stages of works."""

    __tablename__ = "chiffrage_phases"
    __table_args__ = (
        UniqueConstraint("poste_id", "name", name="uq_chiffrage_phases_poste_name"),
        Index("ix_chiffrage_phases_poste_position", "poste_id", "position"),
        Index("ix_chiffrage_phases_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    poste_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chiffrage_postes.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_entity(self) -> ChiffragePhase:
        return ChiffragePhase(
            id=self.id,
            project_id=self.project_id,
            poste_id=self.poste_id,
            name=self.name,
            position=self.position,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, r: ChiffragePhase) -> "ChiffragePhaseModel":
        return cls(
            id=r.id,
            project_id=r.project_id,
            poste_id=r.poste_id,
            name=r.name,
            position=r.position,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    def __repr__(self) -> str:
        return f"<ChiffragePhaseModel {self.id} '{self.name}' project={self.project_id}>"
