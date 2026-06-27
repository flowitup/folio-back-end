"""Labor day description database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class LaborDayDescriptionModel(Base):
    """Labor day description — one free-text entry per (project, date).

    Separate from labor_activities (day title) and labor_entries.note (per-worker).
    Blank description deletes the row (handled at use-case layer).
    """

    __tablename__ = "labor_day_descriptions"

    # One description per project per day — enforced by unique constraint.
    __table_args__ = (UniqueConstraint("project_id", "date", name="uq_labor_day_descriptions_project_date"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    description = Column(Text, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    project = relationship("ProjectModel", backref="labor_day_descriptions")
    creator = relationship("UserModel", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<LaborDayDescription project={self.project_id} date={self.date} description={self.description[:40]!r}>"
