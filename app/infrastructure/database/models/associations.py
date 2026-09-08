"""Association tables for many-to-many relationships."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Table
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.database.models.base import Base

# Association table: users <-> projects (many-to-many).
# A row means "assigned to this project"; what the user may do there comes from
# their company role plus their grant/deny rows, never from this table.
user_projects = Table(
    "user_projects",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "project_id",
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("assigned_at", DateTime, default=lambda: datetime.now(timezone.utc)),
    Column(
        "invited_by_user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
)
