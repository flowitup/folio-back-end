"""SQLAlchemy ORM model for the chiffrage_rooms table."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_room import ChiffrageRoom
from app.infrastructure.database.models.base import Base


class ChiffrageRoomModel(Base):
    """SQLAlchemy mapping for chiffrage_rooms — the project's room vocabulary."""

    __tablename__ = "chiffrage_rooms"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_chiffrage_rooms_project_name"),
        Index("ix_chiffrage_rooms_project_position", "project_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
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

    def to_entity(self) -> ChiffrageRoom:
        return ChiffrageRoom(
            id=self.id,
            project_id=self.project_id,
            name=self.name,
            position=self.position,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, r: ChiffrageRoom) -> "ChiffrageRoomModel":
        return cls(
            id=r.id,
            project_id=r.project_id,
            name=r.name,
            position=r.position,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    def __repr__(self) -> str:
        return f"<ChiffrageRoomModel {self.id} '{self.name}' project={self.project_id}>"
