"""Ports (Protocols) of the chat application layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Optional, Protocol
from uuid import UUID

from app.domain.entities.chat_message import ChannelRef, ChatMessage

# Same minimal session contract as the other bounded contexts (commit / rollback).
from app.application.invitations.ports import TransactionalSessionPort as TransactionalSessionPort  # noqa: F401


@dataclass(frozen=True)
class ChannelInfo:
    """A channel the user can see, resolved from company access / project membership."""

    channel: ChannelRef
    name: str
    member_count: int


@dataclass(frozen=True)
class MemberInfo:
    """A channel member as shown in the chat header (display name falls back to e-mail)."""

    id: UUID
    name: str


class ChatDirectoryPort(Protocol):
    """Membership and naming queries over companies, projects and users."""

    def list_channels_for_user(self, user_id: UUID) -> list[ChannelInfo]:
        """Company channels of every attached company, then project channels of every
        project the user is a member of, owns, or (``*:*``) can see. Sorted by name within kind."""
        ...

    def is_member(self, user_id: UUID, channel: ChannelRef) -> bool:
        """True when the user may read and write the channel."""
        ...

    def channel_exists(self, channel: ChannelRef) -> bool:
        """True when the company / project behind the key exists."""
        ...

    def list_members(self, channel: ChannelRef) -> list[MemberInfo]:
        """Members of the channel, sorted by name."""
        ...

    def display_names(self, user_ids: list[UUID]) -> dict[UUID, str]:
        """Display name (or e-mail) for each user id."""
        ...


class ChatMessageRepositoryPort(Protocol):
    """Persistence of chat messages."""

    def add(self, message: ChatMessage) -> None: ...

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]: ...

    def list_for_channel(self, channel: ChannelRef, before: Optional[datetime], limit: int) -> list[ChatMessage]:
        """Newest ``limit`` messages created before ``before`` (or the newest overall), oldest first."""
        ...

    def count_since(self, channel: ChannelRef, since: Optional[datetime], exclude_sender: UUID) -> int:
        """Messages by other people after ``since`` (all of them when ``since`` is None)."""
        ...

    def last_message_at(self, channel: ChannelRef) -> Optional[datetime]: ...


class ChatReadRepositoryPort(Protocol):
    """Per-user read markers, one per channel."""

    def last_read_at(self, user_id: UUID, channel: ChannelRef) -> Optional[datetime]: ...

    def mark_read(self, user_id: UUID, channel: ChannelRef, at: datetime) -> None: ...

    def last_reads_for_channel(self, channel: ChannelRef) -> dict[UUID, datetime]:
        """Read marker of every user who has read the channel at least once (seen receipts)."""
        ...


class ChatAttachmentStoragePort(Protocol):
    """Binary storage for attachments (the S3 adapter used by invoices / documents satisfies it)."""

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None: ...

    def get_stream(self, key: str) -> tuple[BinaryIO, int]: ...
