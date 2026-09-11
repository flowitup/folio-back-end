"""User database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.associations import user_projects


class UserModel(Base):
    """User database model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    display_name = Column(Text, nullable=True)  # added in phase-01 migration
    phone = Column(String(20), unique=True, nullable=True)  # E.164, SMS-code sign-in
    # flowitup support bypass — not a role, never carried in the token, so
    # revoking it applies on the next request (see app.api.v1.ops_context).
    is_platform_ops = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    # primaryjoin/secondaryjoin required because user_projects now has two FKs
    # to users (user_id + invited_by_user_id); we must pin to user_id only.
    projects = relationship(
        "ProjectModel",
        secondary=user_projects,
        primaryjoin="UserModel.id == user_projects.c.user_id",
        secondaryjoin="ProjectModel.id == user_projects.c.project_id",
        back_populates="users",
        foreign_keys=[user_projects.c.user_id, user_projects.c.project_id],
    )

    # Case-insensitive email index using func.lower()
    __table_args__ = (Index("ix_users_email_lower", func.lower(email)),)

    def __repr__(self) -> str:
        return f"<User {self.email}>"
