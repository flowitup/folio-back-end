"""ORM models for team chat: messages and per-user channel read markers.

Channels are virtual (``channel_kind`` + ``channel_id`` point at a company or a project),
so there is no channel table and no FK on ``channel_id``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage
from app.infrastructure.database.models.base import Base


class ChatMessageOrm(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_channel_created", "channel_kind", "channel_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    channel_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sender_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachment_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachment_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    attachment_content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    attachment_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def to_entity(self) -> ChatMessage:
        attachment = (
            ChatAttachment(
                storage_key=self.attachment_key,
                filename=self.attachment_filename or "image",
                content_type=self.attachment_content_type or "application/octet-stream",
                size_bytes=self.attachment_size_bytes or 0,
            )
            if self.attachment_key
            else None
        )
        created = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=timezone.utc)
        return ChatMessage(
            id=self.id,
            channel=ChannelRef(kind=self.channel_kind, id=self.channel_id),
            sender_id=self.sender_id,
            body=self.body,
            attachment=attachment,
            created_at=created,
        )

    @classmethod
    def from_entity(cls, message: ChatMessage) -> "ChatMessageOrm":
        att = message.attachment
        return cls(
            id=message.id,
            channel_kind=message.channel.kind,
            channel_id=message.channel.id,
            sender_id=message.sender_id,
            body=message.body,
            attachment_key=att.storage_key if att else None,
            attachment_filename=att.filename if att else None,
            attachment_content_type=att.content_type if att else None,
            attachment_size_bytes=att.size_bytes if att else None,
            created_at=message.created_at,
        )


class ChatChannelReadOrm(Base):
    __tablename__ = "chat_channel_reads"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    channel_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    channel_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
