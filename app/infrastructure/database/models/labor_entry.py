"""Labor entry database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class LaborEntryModel(Base):
    """Labor entry database model."""

    __tablename__ = "labor_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    worker_id = Column(UUID(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    amount_override = Column(Numeric(10, 2), nullable=True)
    note = Column(String(500), nullable=True)
    shift_type = Column(String(20), nullable=True)
    supplement_hours = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Phase tag — optional; NULL when entry has no tag. ON DELETE SET NULL keeps
    # the entry when a tag is removed (cost data must not be lost on tag deletion).
    tag_id = Column(
        UUID(as_uuid=True),
        ForeignKey("project_tags.id", ondelete="SET NULL", name="fk_labor_entries_tag_id"),
        nullable=True,
        index=True,
    )

    __table_args__ = (UniqueConstraint("worker_id", "date", name="uq_worker_date"),)

    # Relationships
    worker = relationship("WorkerModel", back_populates="labor_entries")
    tag = relationship("ProjectTagModel", back_populates="labor_entries", foreign_keys=[tag_id])

    def __repr__(self) -> str:
        return f"<LaborEntry worker={self.worker_id} date={self.date}>"
