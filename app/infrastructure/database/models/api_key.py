"""SQLAlchemy ORM model for personal automation API keys.

Maps to the 'api_keys' table. A key inherits its owner's permissions in full
(no scope, no company pinning) and never expires — there is deliberately no
expires_at column (locked product decisions).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.api_key import ApiKey
from app.infrastructure.database.models.base import Base


class ApiKeyOrm(Base):
    """SQLAlchemy mapping for the api_keys table."""

    __tablename__ = "api_keys"
    __table_args__ = (
        # Supports "list my keys, newest first" directly off the index.
        # A plain ix_api_keys_user_id would be redundant: this composite
        # index already leads with user_id, so it serves any query that
        # would have used the single-column index.
        Index("ix_api_keys_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_entity(self) -> ApiKey:
        """Convert ORM model to domain entity.

        SQLite (tests) drops tzinfo on read; coerce naive datetimes back to
        UTC so entity-level comparisons behave the same as against Postgres.
        """
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        last_used_at = self.last_used_at
        if last_used_at is not None and last_used_at.tzinfo is None:
            last_used_at = last_used_at.replace(tzinfo=timezone.utc)
        return ApiKey(
            id=self.id,
            user_id=self.user_id,
            name=self.name,
            prefix=self.prefix,
            token_hash=self.token_hash,
            created_at=created_at,
            last_used_at=last_used_at,
        )

    @classmethod
    def from_entity(cls, api_key: ApiKey) -> "ApiKeyOrm":
        """Convert domain entity to ORM model (insert path)."""
        return cls(
            id=api_key.id,
            user_id=api_key.user_id,
            name=api_key.name,
            prefix=api_key.prefix,
            token_hash=api_key.token_hash,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
        )

    def __repr__(self) -> str:
        return f"<ApiKeyOrm {self.id} '{self.name}' prefix={self.prefix}>"
