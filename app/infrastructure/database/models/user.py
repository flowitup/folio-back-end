"""User database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.associations import user_roles, user_projects


class UserModel(Base):
    """User database model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)  # Argon2 hashes are ~97 chars
    is_active = Column(Boolean, default=True, nullable=False)
    display_name = Column(Text, nullable=True)  # added in phase-01 migration
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    roles = relationship("RoleModel", secondary=user_roles, back_populates="users")
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
