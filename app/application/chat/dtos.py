"""Read models returned by the chat use-cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.chat_message import ChatMessage


@dataclass(frozen=True)
class ChannelDto:
    key: str
    kind: str
    id: UUID
    name: str
    member_count: int
    unread_count: int
    last_message_at: datetime | None


@dataclass(frozen=True)
class MemberDto:
    id: UUID
    name: str
    # When the member last read the channel; None until they open it. Drives "seen" avatars.
    last_read_at: datetime | None = None


@dataclass(frozen=True)
class AttachmentDto:
    filename: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class MessageDto:
    id: UUID
    channel_key: str
    sender_id: UUID
    sender_name: str
    body: str | None
    attachment: AttachmentDto | None
    created_at: datetime

    @classmethod
    def from_entity(cls, message: ChatMessage, sender_name: str) -> MessageDto:
        return cls(
            id=message.id,
            channel_key=message.channel.key,
            sender_id=message.sender_id,
            sender_name=sender_name,
            body=message.body,
            attachment=(
                AttachmentDto(
                    filename=message.attachment.filename,
                    content_type=message.attachment.content_type,
                    size_bytes=message.attachment.size_bytes,
                )
                if message.attachment
                else None
            ),
            created_at=message.created_at,
        )


@dataclass(frozen=True)
class MessagePageDto:
    """A page of messages plus the channel members for the header."""

    items: list[MessageDto]
    members: list[MemberDto]


@dataclass(frozen=True)
class AttachmentStreamDto:
    """Attachment bytes ready to stream."""

    stream: object
    content_length: int
    content_type: str
    filename: str
