"""Labor payment note database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.database.models.base import Base


class LaborPaymentNoteModel(Base):
    """One free-text note per (project, worker, month) on the labor Payments tab.

    ``month`` stores the first day of the month. Blank notes delete the row
    (handled at the use-case layer).
    """

    __tablename__ = "labor_payment_notes"

    __table_args__ = (
        UniqueConstraint("project_id", "worker_id", "month", name="uq_labor_payment_notes_project_worker_month"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    worker_id = Column(UUID(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), nullable=False, index=True)
    month = Column(Date, nullable=False)
    note = Column(Text, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<LaborPaymentNote worker={self.worker_id} month={self.month} note={self.note[:40]!r}>"
