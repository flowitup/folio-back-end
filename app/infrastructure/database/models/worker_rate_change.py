"""WorkerRateChange database model.

Maps to the ``worker_rate_changes`` table which holds effective-dated
daily-rate overrides for individual workers.  The rate applicable on
date D for worker W is the row with the greatest effective_date <= D;
if no row exists, callers fall back to ``workers.daily_rate``.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class WorkerRateChangeModel(Base):
    """Effective-dated daily-rate change for a worker."""

    __tablename__ = "worker_rate_changes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    worker_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    effective_date = Column(Date, nullable=False)
    daily_rate = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("worker_id", "effective_date", name="uq_worker_rate_effective"),
        Index("ix_worker_rate_changes_worker_date", "worker_id", "effective_date"),
    )

    # Relationship back to WorkerModel — optional; back_populates declared
    # on WorkerModel.rate_changes (cascade="all, delete-orphan").
    worker = relationship("WorkerModel", back_populates="rate_changes")

    def __repr__(self) -> str:
        return f"<WorkerRateChange worker={self.worker_id} eff={self.effective_date} rate={self.daily_rate}>"
