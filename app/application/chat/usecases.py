"""Chat use-cases: list channels, list messages, send, mark read, stream an attachment.

Authorization is enforced here (not only at the route): every channel operation checks
``ChatDirectoryPort.is_member`` and raises ``NotChannelMemberError`` (→ 403).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, BinaryIO, Optional
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
    ReplyTargetNotInChannelError,
    UnsupportedAttachmentTypeError,
)
from app.application.chat.ports import (
    ChatAttachmentStoragePort,
    ChatDirectoryPort,
    ChatMessageRepositoryPort,
    ChatReadRepositoryPort,
    TransactionalSessionPort,
)
from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage, mentions_assistant_token

ALLOWED_IMAGE_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})
# Voice notes: an AAC/m4a recording reaches us under whichever spelling the recording device
# picked, so accept every name iOS and Android give the same container.
ALLOWED_AUDIO_TYPES: frozenset[str] = frozenset(
    {"audio/aac", "audio/m4a", "audio/x-m4a", "audio/mp4", "audio/mp4a-latm", "audio/mpeg"}
)
ALLOWED_ATTACHMENT_TYPES: frozenset[str] = ALLOWED_IMAGE_TYPES | ALLOWED_AUDIO_TYPES
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

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        read_repo: ChatReadRepositoryPort,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._reads = read_repo

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
        names = self._directory.display_names([m.sender_id for m in messages if m.sender_id is not None])
        items = [
            MessageDto.from_entity(m, names.get(m.sender_id, "?") if m.sender_id is not None else "Folio")
            for m in messages
        ]
        reads = self._reads.last_reads_for_channel(channel)
        members = [
            MemberDto(id=m.id, name=m.name, last_read_at=reads.get(m.id)) for m in self._directory.list_members(channel)
        ]
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
        notifier: Any = None,
        assistant_dispatcher: Any = None,
    ) -> None:
        # Public: the push stack (and the assistant dispatcher) is constructed after the
        # chat use cases in create_app(), so both are attached afterwards rather than
        # passed in here.
        self._directory = directory
        self._messages = message_repo
        self._reads = read_repo
        self._storage = storage
        self._db = db_session
        self.notifier = notifier
        self.assistant_dispatcher = assistant_dispatcher

    def execute(
        self,
        *,
        actor_id: UUID,
        channel_key: str,
        body: str | None,
        attachment: tuple[str, str, bytes] | None = None,
        lang: str | None = None,
        reply_to_id: UUID | None = None,
    ) -> MessageDto:
        """``attachment`` is ``(filename, content_type, data)``.

        ``lang`` (vi|fr|en), when given, is stored on the message payload as
        ``{"lang": lang}`` so a dispatched assistant reply answers in the right language,
        whatever the channel kind.

        ``reply_to_id``, when given, must name a message of this same channel (else
        ``ReplyTargetNotInChannelError``); it is what lets a reply to an assistant
        message dispatch even without an ``@folio`` mention (D18) — see
        ``mentions_assistant`` below.

        The message dispatches to the assistant pipeline only when its body/caption
        mentions ``@folio`` or it replies to an assistant-authored message — never for
        any other message, in any channel kind.

        Raises:
            ChatChannelNotFoundError, NotChannelMemberError, EmptyMessageError,
            UnsupportedAttachmentTypeError, AttachmentTooLargeError, ReplyTargetNotInChannelError.
        """
        channel = _parse_channel(channel_key)
        _require_member(self._directory, actor_id, channel)

        reply_target: ChatMessage | None = None
        if reply_to_id is not None:
            reply_target = self._messages.find_by_id(reply_to_id)
            if reply_target is None or reply_target.channel != channel:
                raise ReplyTargetNotInChannelError("reply_to_id not in this channel")

        stored: ChatAttachment | None = None
        if attachment is not None:
            filename, content_type, data = attachment
            if content_type not in ALLOWED_ATTACHMENT_TYPES:
                raise UnsupportedAttachmentTypeError(f"Unsupported attachment type '{content_type}'.")
            if len(data) == 0 or len(data) > MAX_ATTACHMENT_BYTES:
                raise AttachmentTooLargeError(f"Attachment must be 1..{MAX_ATTACHMENT_BYTES} bytes.")
            stored = ChatAttachment(
                storage_key="",  # set once the message id is known
                filename=filename[:255] or "attachment",
                content_type=content_type,
                size_bytes=len(data),
            )

        mentions = bool(body and mentions_assistant_token(body))
        if not mentions and reply_target is not None and reply_target.sender_type == "assistant":
            mentions = True

        payload = {"lang": lang} if lang else None
        try:
            message = ChatMessage.create(
                channel=channel,
                sender_id=actor_id,
                body=body,
                attachment=stored,
                payload=payload,
                reply_to_id=reply_to_id,
                mentions_assistant=mentions,
            )
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
                sender_type=message.sender_type,
                content_type=message.content_type,
                payload=message.payload,
                reply_to_id=message.reply_to_id,
                mentions_assistant=message.mentions_assistant,
                ai_trace_id=message.ai_trace_id,
            )

        self._messages.add(message)
        # Sending implies having seen the channel up to now.
        self._reads.mark_read(actor_id, channel, message.created_at)
        self._db.commit()
        # After the commit: a push (or the assistant hand-off) must never be able to
        # roll back the message.
        if self.notifier is not None:
            self.notifier.message_sent(
                channel=channel,
                sender_id=actor_id,
                preview=message.body,
                sent_at=message.created_at,
            )
        if self.assistant_dispatcher is not None and message.mentions_assistant and message.sender_type == "user":
            self.assistant_dispatcher.message_received(user_id=actor_id, message_id=message.id)
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
    "ALLOWED_AUDIO_TYPES",
    "ALLOWED_IMAGE_TYPES",
    "MAX_ATTACHMENT_BYTES",
    "GetAttachmentUseCase",
    "ListChannelsUseCase",
    "ListMessagesUseCase",
    "MarkChannelReadUseCase",
    "SendMessageUseCase",
    "BinaryIO",
]
