"""Labor entry database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import text
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

    # Validation workflow: manager-logged rows are born 'validated'; rows a
    # worker logs for themselves start 'pending' until a manager validates
    # (or rejects, which deletes the row). Only validated rows are priced.
    status = Column(String(20), nullable=False, default="validated", server_default="validated")
    submitted_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_labor_entries_submitted_by"),
        nullable=True,
    )
    validated_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_labor_entries_validated_by"),
        nullable=True,
    )
    validated_at = Column(DateTime(timezone=True), nullable=True)
    # Worker-requested change on a validated day (see migration c7d8e9f0a1b2).
    proposed_shift_type = Column(String(20), nullable=True)
    proposed_supplement_hours = Column(Integer, nullable=True)
    proposed_note = Column(String(500), nullable=True)
    change_requested_at = Column(DateTime(timezone=True), nullable=True)
    change_requested_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_labor_entries_change_requested_by"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("worker_id", "date", name="uq_worker_date"),
        CheckConstraint("status IN ('pending', 'validated')", name="ck_labor_entries_status"),
        Index("ix_labor_entries_pending", "worker_id", postgresql_where=text("status = 'pending'")),
        Index(
            "ix_labor_entries_change_requested",
            "worker_id",
            postgresql_where=text("change_requested_at IS NOT NULL"),
        ),
    )

    # Relationships
    worker = relationship("WorkerModel", back_populates="labor_entries")

    def __repr__(self) -> str:
        return f"<LaborEntry worker={self.worker_id} date={self.date}>"
