"""SQLAlchemy ORM model for the chiffrage_units table."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chiffrage_unit import ChiffrageUnit
from app.infrastructure.database.models.base import Base


class ChiffrageUnitModel(Base):
    """SQLAlchemy mapping for chiffrage_units — user-added units of measure.

    Holds custom units only; the preset symbols are an application constant and
    are never persisted as rows.
    """

    __tablename__ = "chiffrage_units"
    __table_args__ = (
        UniqueConstraint("project_id", "symbol", name="uq_chiffrage_units_project_symbol"),
        Index("ix_chiffrage_units_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_entity(self) -> ChiffrageUnit:
        return ChiffrageUnit(
            id=self.id,
            project_id=self.project_id,
            symbol=self.symbol,
            created_at=self.created_at,
        )

    @classmethod
    def from_entity(cls, u: ChiffrageUnit) -> "ChiffrageUnitModel":
        return cls(id=u.id, project_id=u.project_id, symbol=u.symbol, created_at=u.created_at)

    def __repr__(self) -> str:
        return f"<ChiffrageUnitModel {self.id} '{self.symbol}' project={self.project_id}>"
