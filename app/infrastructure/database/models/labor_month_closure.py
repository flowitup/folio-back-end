"""Closed labor months ("Chốt tháng") and the writes recorded after closing."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.database.models.base import Base


class LaborMonthClosureModel(Base):
    """One closed (project, month). ``month`` stores the first day of the month.

    A soft marker: writes into the month still succeed and are logged in
    ``labor_month_closure_edits``. Reopening deletes the row (and its edits).
    """

    __tablename__ = "labor_month_closures"

    __table_args__ = (UniqueConstraint("project_id", "month", name="uq_labor_month_closures_project_month"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    month = Column(Date, nullable=False)
    closed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<LaborMonthClosure project={self.project_id} month={self.month}>"


class LaborMonthClosureEditModel(Base):
    """One write that landed in a closed month: who, when, what kind, which entry/worker."""

    __tablename__ = "labor_month_closure_edits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    closure_id = Column(
        UUID(as_uuid=True),
        ForeignKey("labor_month_closures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    edited_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    kind = Column(String(32), nullable=False)
    # A labor entry, labor payment (invoice) or rate change id. No foreign key: the
    # record must outlive a deleted entry, which is itself one of the edits logged.
    entry_id = Column(UUID(as_uuid=True), nullable=True)
    worker_id = Column(UUID(as_uuid=True), ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    entry_date = Column(Date, nullable=True)

    def __repr__(self) -> str:
        return f"<LaborMonthClosureEdit closure={self.closure_id} kind={self.kind}>"
