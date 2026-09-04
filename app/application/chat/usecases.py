"""Chat use-cases: list channels, list messages, send, mark read, stream an attachment.

Authorization is enforced here (not only at the route): every channel operation checks
``ChatDirectoryPort.is_member`` and raises ``NotChannelMemberError`` (→ 403).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import BinaryIO, Optional
from uuid import UUID

from app.application.chat.dtos import (
    AttachmentStreamDto,
    ChannelDto,
    MemberDto,
    MessageDto,
    MessagePageDto,
)
from app.application.chat.exceptions import (
    AttachmentTooLargeError,
    ChatChannelNotFoundError,
    ChatMessageNotFoundError,
    EmptyMessageError,
    NotChannelMemberError,
    UnsupportedAttachmentTypeError,
)
from app.application.chat.ports import (
    ChatAttachmentStoragePort,
    ChatDirectoryPort,
    ChatMessageRepositoryPort,
    ChatReadRepositoryPort,
    TransactionalSessionPort,
)
from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage

ALLOWED_ATTACHMENT_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _parse_channel(key: str) -> ChannelRef:
    try:
        return ChannelRef.parse(key)
    except ValueError as exc:
        raise ChatChannelNotFoundError(str(exc)) from exc


def _require_member(directory: ChatDirectoryPort, actor_id: UUID, channel: ChannelRef) -> None:
    if not directory.channel_exists(channel):
        raise ChatChannelNotFoundError(f"Channel {channel.key} does not exist.")
    if not directory.is_member(actor_id, channel):
        raise NotChannelMemberError(f"User {actor_id} is not a member of channel {channel.key}.")


class ListChannelsUseCase:
    """Every channel the actor belongs to, with unread counts."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        read_repo: ChatReadRepositoryPort,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._reads = read_repo

    def execute(self, *, actor_id: UUID) -> list[ChannelDto]:
        result: list[ChannelDto] = []
        for info in self._directory.list_channels_for_user(actor_id):
            since = self._reads.last_read_at(actor_id, info.channel)
            result.append(
                ChannelDto(
                    key=info.channel.key,
                    kind=info.channel.kind,
                    id=info.channel.id,
                    name=info.name,
                    member_count=info.member_count,
                    unread_count=self._messages.count_since(info.channel, since, exclude_sender=actor_id),
                    last_message_at=self._messages.last_message_at(info.channel),
                )
            )
        return result


class ListMessagesUseCase:
    """A page of a channel's messages (oldest first) plus its members."""

    def __init__(self, directory: ChatDirectoryPort, message_repo: ChatMessageRepositoryPort) -> None:
        self._directory = directory
        self._messages = message_repo

    def execute(
        self,
        *,
        actor_id: UUID,
        channel_key: str,
        before: Optional[datetime] = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> MessagePageDto:
        channel = _parse_channel(channel_key)
        _require_member(self._directory, actor_id, channel)
        page_size = max(1, min(limit, MAX_PAGE_SIZE))
        messages = self._messages.list_for_channel(channel, before, page_size)
        names = self._directory.display_names([m.sender_id for m in messages])
        items = [MessageDto.from_entity(m, names.get(m.sender_id, "?")) for m in messages]
        members = [MemberDto(id=m.id, name=m.name) for m in self._directory.list_members(channel)]
        return MessagePageDto(items=items, members=members)


class SendMessageUseCase:
    """Persist a text and/or image message; the attachment bytes go to object storage first."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        read_repo: ChatReadRepositoryPort,
        storage: ChatAttachmentStoragePort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._reads = read_repo
        self._storage = storage
        self._db = db_session

    def execute(
        self,
        *,
        actor_id: UUID,
        channel_key: str,
        body: str | None,
        attachment: tuple[str, str, bytes] | None = None,
    ) -> MessageDto:
        """``attachment`` is ``(filename, content_type, data)``.

        Raises:
            ChatChannelNotFoundError, NotChannelMemberError, EmptyMessageError,
            UnsupportedAttachmentTypeError, AttachmentTooLargeError.
        """
        channel = _parse_channel(channel_key)
        _require_member(self._directory, actor_id, channel)

        stored: ChatAttachment | None = None
        if attachment is not None:
            filename, content_type, data = attachment
            if content_type not in ALLOWED_ATTACHMENT_TYPES:
                raise UnsupportedAttachmentTypeError(f"Unsupported attachment type '{content_type}'.")
            if len(data) == 0 or len(data) > MAX_ATTACHMENT_BYTES:
                raise AttachmentTooLargeError(f"Attachment must be 1..{MAX_ATTACHMENT_BYTES} bytes.")
            stored = ChatAttachment(
                storage_key="",  # set once the message id is known
                filename=filename[:255] or "image",
                content_type=content_type,
                size_bytes=len(data),
            )

        try:
            message = ChatMessage.create(channel=channel, sender_id=actor_id, body=body, attachment=stored)
        except ValueError as exc:
            raise EmptyMessageError(str(exc)) from exc

        if stored is not None and attachment is not None:
            key = f"chat/{channel.kind}/{channel.id}/{message.id}"
            self._storage.put(key, io.BytesIO(attachment[2]), stored.content_type)
            message = ChatMessage(
                id=message.id,
                channel=message.channel,
                sender_id=message.sender_id,
                body=message.body,
                attachment=ChatAttachment(
                    storage_key=key,
                    filename=stored.filename,
                    content_type=stored.content_type,
                    size_bytes=stored.size_bytes,
                ),
                created_at=message.created_at,
            )

        self._messages.add(message)
        # Sending implies having seen the channel up to now.
        self._reads.mark_read(actor_id, channel, message.created_at)
        self._db.commit()
        names = self._directory.display_names([actor_id])
        return MessageDto.from_entity(message, names.get(actor_id, "?"))


class MarkChannelReadUseCase:
    """Move the actor's read marker of a channel to now."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        read_repo: ChatReadRepositoryPort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._directory = directory
        self._reads = read_repo
        self._db = db_session

    def execute(self, *, actor_id: UUID, channel_key: str) -> None:
        channel = _parse_channel(channel_key)
        _require_member(self._directory, actor_id, channel)
        self._reads.mark_read(actor_id, channel, datetime.now(timezone.utc))
        self._db.commit()


class GetAttachmentUseCase:
    """Stream a message attachment to a channel member."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        storage: ChatAttachmentStoragePort,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._storage = storage

    def execute(self, *, actor_id: UUID, message_id: UUID) -> AttachmentStreamDto:
        message = self._messages.find_by_id(message_id)
        if message is None or message.attachment is None:
            raise ChatMessageNotFoundError(f"Message {message_id} has no attachment.")
        _require_member(self._directory, actor_id, message.channel)
        stream, length = self._storage.get_stream(message.attachment.storage_key)
        return AttachmentStreamDto(
            stream=stream,
            content_length=length,
            content_type=message.attachment.content_type,
            filename=message.attachment.filename,
        )


__all__ = [
    "ALLOWED_ATTACHMENT_TYPES",
    "MAX_ATTACHMENT_BYTES",
    "GetAttachmentUseCase",
    "ListChannelsUseCase",
    "ListMessagesUseCase",
    "MarkChannelReadUseCase",
    "SendMessageUseCase",
    "BinaryIO",
]
