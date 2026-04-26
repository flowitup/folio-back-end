"""Task database model — Kanban planning."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base

# Use JSONB on PostgreSQL, generic JSON elsewhere (SQLite for tests)
LabelsJSON = JSON().with_variant(JSONB(), "postgresql")


class TaskModel(Base):
    """ORM model for the `tasks` table (Kanban board entries)."""

    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, server_default="backlog")
    priority = Column(String(10), nullable=False, server_default="medium")
    assignee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    due_date = Column(Date, nullable=True)
    # Integer position with gaps (e.g. 1000, 2000, ...) so drag-drop can insert
    # between two cards without renumbering the whole column. Periodic rebalance
    # required if gaps shrink to 0 — handled in the use case.
    position = Column(Integer, nullable=False, server_default="0")
    labels = Column(LabelsJSON, nullable=False, default=list)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    project = relationship("ProjectModel", foreign_keys=[project_id])
    assignee = relationship("UserModel", foreign_keys=[assignee_id])
    creator = relationship("UserModel", foreign_keys=[created_by])

    __table_args__ = (
        # Whitelist enums at the DB level so a buggy client can't write garbage.
        CheckConstraint(
            "status IN ('backlog','todo','in_progress','blocked','done')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('low','medium','high','urgent')",
            name="ck_tasks_priority",
        ),
        # Composite index — board reads filter by project + status, ordered by position.
        Index("ix_tasks_project_status_position", "project_id", "status", "position"),
    )

    def __repr__(self) -> str:
        return f"<Task {self.title!r} status={self.status} project={self.project_id}>"
