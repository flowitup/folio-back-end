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

    def shares_channel(self, user_id: UUID, other_id: UUID) -> bool:
        """True when both users can read at least one common channel."""
        ...

    def list_members(self, channel: ChannelRef) -> list[MemberInfo]:
        """Members of the channel, sorted by name."""
        ...

    def channel_name(self, channel: ChannelRef) -> str:
        """Display name of the company / project behind the key."""
        ...

    def display_names(self, user_ids: list[UUID]) -> dict[UUID, str]:
        """Display name (or e-mail) for each user id."""
        ...

    def account_states(self, user_ids: list[UUID]) -> dict[UUID, str]:
        """``"deleted"`` (erased account) or ``"unnamed"`` (no display name) for the ids that are
        shown by a placeholder name; absent for everyone else. Lets the apps translate the label."""
        ...

    def joined_at_many(self, user_id: UUID, channels: list[ChannelRef]) -> dict[str, datetime]:
        """When the user gained access to each channel (by key), where the data tells."""
        ...

    def channel_company_id(self, channel: ChannelRef) -> Optional[UUID]: ...

    def company_admin_ids(self, company_id: UUID) -> list[UUID]: ...

    def company_channels(self, company_id: UUID) -> list[ChannelRef]: ...


class ChatMessageRepositoryPort(Protocol):
    """Persistence of chat messages."""

    def add(self, message: ChatMessage) -> None: ...

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]: ...

    def list_for_channel(
        self,
        channel: ChannelRef,
        before: Optional[datetime],
        limit: int,
        exclude_senders: Optional[set[UUID]] = None,
    ) -> list[ChatMessage]:
        """Newest ``limit`` messages created before ``before`` (or the newest overall), oldest first,
        leaving out ``exclude_senders`` before the limit applies."""
        ...

    def count_since_many(
        self,
        thresholds: dict[ChannelRef, Optional[datetime]],
        exclude_sender: UUID,
        exclude_senders: Optional[set[UUID]] = None,
    ) -> dict[str, int]:
        """``count_since`` of many channels in one go, by channel key (absent = 0)."""
        ...

    def last_message_at_many(self, channels: list[ChannelRef]) -> dict[str, datetime]: ...

    def delete_message(self, message_id: UUID) -> None: ...

    def count_since(self, channel: ChannelRef, since: Optional[datetime], exclude_sender: UUID) -> int:
        """Messages by other people after ``since`` (all of them when ``since`` is None).

        A legacy message with a NULL ``sender_id`` always counts as "by someone else",
        regardless of ``exclude_sender``.
        """
        ...

    def last_message_at(self, channel: ChannelRef) -> Optional[datetime]: ...


class ChatReadRepositoryPort(Protocol):
    """Per-user read markers, one per channel."""

    def last_read_at(self, user_id: UUID, channel: ChannelRef) -> Optional[datetime]: ...

    def mark_read(self, user_id: UUID, channel: ChannelRef, at: datetime) -> None: ...

    def last_reads_for_user(self, user_id: UUID) -> dict[str, datetime]:
        """Every read marker of the user, by channel key."""
        ...

    def last_reads_for_channel(self, channel: ChannelRef) -> dict[UUID, datetime]:
        """Read marker of every user who has read the channel at least once (seen receipts)."""
        ...


@dataclass(frozen=True)
class ReactionInfo:
    """One member's emoji on a message."""

    user_id: UUID
    emoji: str
    created_at: datetime


class ChatReactionRepositoryPort(Protocol):
    """Emoji reactions on messages, at most one per person per message."""

    def set_reaction(self, message_id: UUID, user_id: UUID, emoji: str) -> None:
        """Add the person's reaction or replace the one they already left."""
        ...

    def remove_reaction(self, message_id: UUID, user_id: UUID) -> None: ...

    def reactions_for_messages(self, message_ids: list[UUID]) -> dict[UUID, list[ReactionInfo]]:
        """Reactions of the given messages, oldest first; messages without any are absent."""
        ...


class ChatAttachmentStoragePort(Protocol):
    """Binary storage for attachments (the S3 adapter used by invoices / documents satisfies it)."""

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None: ...

    def get_stream(self, key: str) -> tuple[BinaryIO, int]: ...

    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class ReportInfo:
    """A member's report of a message, with the message as it stands."""

    id: UUID
    message: ChatMessage
    reporter_id: UUID
    reason: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime] = None


class ChatSafetyRepositoryPort(Protocol):
    """Reports of messages and per-user blocks (App Store guideline 1.2)."""

    def add_report(self, message_id: UUID, reporter_id: UUID, reason: Optional[str]) -> bool:
        """Store the report; False when this person had already reported the message."""
        ...

    def list_open_reports(self, channels: list[ChannelRef]) -> list[ReportInfo]: ...

    def find_report(self, report_id: UUID) -> Optional[ReportInfo]: ...

    def resolve_reports_of_message(self, message_id: UUID, at: datetime) -> None: ...

    def block(self, blocker_id: UUID, blocked_id: UUID) -> None: ...

    def unblock(self, blocker_id: UUID, blocked_id: UUID) -> None: ...

    def blocked_ids(self, blocker_id: UUID) -> set[UUID]: ...

    def blocker_ids_of(self, blocked_id: UUID) -> set[UUID]:
        """Users who blocked ``blocked_id`` (they must not be pushed about its messages)."""
        ...
