"""
SQLAlchemy ORM models for authentication.

These models map to PostgreSQL tables and handle persistence.
Domain entities are pure Python; these models bridge to the database.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Table, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


# Association table: users <-> roles (many-to-many)
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("assigned_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)

# Association table: roles <-> permissions (many-to-many)
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,  # Reverse index for permission lookups
    ),
)


class UserModel(Base):
    """User database model."""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)  # Argon2 hashes are ~97 chars
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    roles = relationship("RoleModel", secondary=user_roles, back_populates="users")

    # Case-insensitive email index using func.lower()
    __table_args__ = (Index("ix_users_email_lower", func.lower(email)),)

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class RoleModel(Base):
    """Role database model."""
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    users = relationship("UserModel", secondary=user_roles, back_populates="roles")
    permissions = relationship(
        "PermissionModel", secondary=role_permissions, back_populates="roles"
    )

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


class PermissionModel(Base):
    """Permission database model."""
    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(100), unique=True, nullable=False)  # e.g., 'project:create'
    resource = Column(String(50), nullable=False)  # e.g., 'project'
    action = Column(String(50), nullable=False)  # e.g., 'create'
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    roles = relationship(
        "RoleModel", secondary=role_permissions, back_populates="permissions"
    )

    __table_args__ = (Index("ix_permissions_resource_action", "resource", "action"),)

    def __repr__(self) -> str:
        return f"<Permission {self.name}>"
