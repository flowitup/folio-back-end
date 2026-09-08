"""When each user was last pushed about each chat channel.

Chat is the one notification source that can fire many times a minute, so pushes are
coalesced per (user, channel): the marker is what makes the quiet window decidable without
re-reading message history.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class ChatPushMarkerModel(Base):
    __tablename__ = "chat_push_markers"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Denormalised `<kind>:<uuid>` key: channels are derived from companies/projects and
    # have no table of their own, so there is nothing to reference.
    channel_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
