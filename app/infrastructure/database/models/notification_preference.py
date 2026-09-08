"""ORM row holding one user's push notification opt-outs.

Absence of a row means "everything on": defaults live here, not in a backfill, so a new
user needs no write and an existing user is unaffected by the migration.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class NotificationPreferenceModel(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Master switch; when false no category matters.
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    chat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    attendance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    tasks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    membership: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    billing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
