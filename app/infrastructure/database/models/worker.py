"""Worker database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class WorkerModel(Base):
    """Worker database model."""

    __tablename__ = "workers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # Person FK is nullable during the Phase 1 rollout: the column ships
    # additive (migration b1c2d3e4f5a6) and is backfilled by a dedicated
    # script in Phase 1c. A follow-up migration tightens it to NOT NULL
    # once 100% of rows are populated. ondelete=RESTRICT mirrors the FK
    # declared in the migration — a Person referenced by any Worker
    # cannot be deleted; admins must use the merge tool first.
    person_id = Column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    daily_rate = Column(Numeric(10, 2), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    project = relationship("ProjectModel")
    person = relationship("PersonModel", back_populates="workers")
    labor_entries = relationship("LaborEntryModel", back_populates="worker", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Worker {self.name}>"
