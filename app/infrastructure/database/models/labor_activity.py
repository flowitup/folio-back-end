"""Labor activity database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class LaborActivityModel(Base):
    """Labor activity database model — one entry per (project, date)."""

    __tablename__ = "labor_activities"

    # One activity per project per day — enforced by unique constraint.
    __table_args__ = (UniqueConstraint("project_id", "date", name="uq_labor_activities_project_date"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    title = Column(Text, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    project = relationship("ProjectModel", backref="labor_activities")
    creator = relationship("UserModel", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<LaborActivity project={self.project_id} date={self.date} title={self.title!r}>"
