"""Chat use-cases: list channels, list messages, send, mark read, stream an attachment.

Authorization is enforced here (not only at the route): every channel operation checks
``ChatDirectoryPort.is_member`` and raises ``NotChannelMemberError`` (→ 403).
"""

from __future__ import annotations

import io
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, BinaryIO, Optional
from uuid import UUID

from app.application.chat.dtos import (
    AttachmentStreamDto,
    ChannelDto,
    MemberDto,
    MessageDto,
    MessagePageDto,
    ReactionDto,
    ReportDto,
)
from app.application.chat.exceptions import (
    BlockTargetNotFoundError,
    CannotBlockSelfError,
    AttachmentContentMismatchError,
    AttachmentTooLargeError,
    CannotReportOwnMessageError,
    ChatChannelNotFoundError,
    ChatReportNotFoundError,
    NotCompanyAdminError,
    ChatMessageNotFoundError,
    EmptyMessageError,
    InvalidReactionError,
    NotChannelMemberError,
    ReplyTargetNotInChannelError,
    UnsupportedAttachmentTypeError,
)
from app.application.chat.ports import (
    ChatAttachmentStoragePort,
    ChatDirectoryPort,
    ChatMessageRepositoryPort,
    ChatReactionRepositoryPort,
    ChatReadRepositoryPort,
    ChatSafetyRepositoryPort,
    TransactionalSessionPort,
)
from app.domain.entities.chat_message import REACTION_EMOJIS, ChannelRef, ChatAttachment, ChatMessage
from app.domain.value_objects.display_filename import strip_control_chars
from app.domain.value_objects.image_signature import sniff_image_type

logger = logging.getLogger(__name__)

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


def _reaction_dtos(
    reactions: list[Any], blocked: set[UUID], names: Optional[dict[UUID, str]] = None
) -> tuple[ReactionDto, ...]:
    """Reactions as shown to one reader: people they blocked leave no trace."""
    return tuple(
        ReactionDto(
            user_id=r.user_id,
            emoji=r.emoji,
            created_at=r.created_at,
            user_name=(names or {}).get(r.user_id),
        )
        for r in reactions
        if r.user_id not in blocked
    )


def _account_states(directory: ChatDirectoryPort, user_ids: list[UUID]) -> dict[UUID, str]:
    reader = getattr(directory, "account_states", None)
    return reader(user_ids) if reader is not None and user_ids else {}


def _sniffed_attachment_type(declared: str, data: bytes) -> Optional[str]:
    """The type the bytes really are, within what chat accepts; None when they are not.

    The declared type is the client's word: an HTML page labelled image/png must not be stored
    and served back as a photo. Images are told apart by signature (and stored under the type
    they really are); voice notes by the MP4/3GP ``ftyp`` box, an ID3 tag or an MPEG/ADTS sync."""
    head = data[:16]
    if declared in ALLOWED_IMAGE_TYPES:
        return sniff_image_type(head)
    if declared in ALLOWED_AUDIO_TYPES:
        if head[4:8] == b"ftyp" or head.startswith(b"ID3"):
            return declared
        if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
            return declared
    return None


def _reactions_with_names(
    directory: ChatDirectoryPort, reactions: dict[UUID, list[Any]], blocked: set[UUID]
) -> dict[UUID, tuple[ReactionDto, ...]]:
    reactor_ids = list({r.user_id for rs in reactions.values() for r in rs if r.user_id not in blocked})
    names = directory.display_names(reactor_ids) if reactor_ids else {}
    return {mid: _reaction_dtos(rs, blocked, names) for mid, rs in reactions.items()}


class ListChannelsUseCase:
    """Every channel the actor belongs to, with unread counts."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        read_repo: ChatReadRepositoryPort,
        safety_repo: Optional[ChatSafetyRepositoryPort] = None,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._reads = read_repo
        self._safety = safety_repo

    def execute(self, *, actor_id: UUID) -> list[ChannelDto]:
        blocked = self._safety.blocked_ids(actor_id) if self._safety is not None else set()
        infos = self._directory.list_channels_for_user(actor_id)
        channels = [info.channel for info in infos]
        reads = self._reads.last_reads_for_user(actor_id)
        # A channel never opened counts from when the person joined it, not from its first message:
        # a newcomer is not greeted with the whole history as unread.
        joined = self._directory.joined_at_many(actor_id, channels) if channels else {}
        thresholds = {c: reads.get(c.key) or joined.get(c.key) for c in channels}
        unread = (
            self._messages.count_since_many(thresholds, exclude_sender=actor_id, exclude_senders=blocked)
            if channels
            else {}
        )
        newest = self._messages.last_message_at_many(channels) if channels else {}
        return [
            ChannelDto(
                key=info.channel.key,
                kind=info.channel.kind,
                id=info.channel.id,
                name=info.name,
                member_count=info.member_count,
                unread_count=unread.get(info.channel.key, 0),
                last_message_at=newest.get(info.channel.key),
            )
            for info in infos
        ]


class ListMessagesUseCase:
    """A page of a channel's messages (oldest first) plus its members."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        read_repo: ChatReadRepositoryPort,
        safety_repo: Optional[ChatSafetyRepositoryPort] = None,
        reaction_repo: Optional[ChatReactionRepositoryPort] = None,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._reads = read_repo
        self._safety = safety_repo
        self._reactions = reaction_repo

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
        blocked = self._safety.blocked_ids(actor_id) if self._safety is not None else set()
        # Blocked senders are left out in the query, before the limit: a page is always
        # ``limit`` visible messages, so paging with ``before`` reaches the whole history.
        messages = self._messages.list_for_channel(channel, before, page_size, exclude_senders=blocked or None)
        sender_ids = list({m.sender_id for m in messages if m.sender_id is not None})
        names = self._directory.display_names(sender_ids)
        reactions = self._reactions.reactions_for_messages([m.id for m in messages]) if self._reactions else {}
        reaction_dtos = _reactions_with_names(self._directory, reactions, blocked)
        member_infos = self._directory.list_members(channel)
        states = _account_states(self._directory, list({*sender_ids, *(m.id for m in member_infos)}))
        items = [
            replace(
                MessageDto.from_entity(m, names.get(m.sender_id, "?") if m.sender_id is not None else "Folio"),
                reactions=reaction_dtos.get(m.id, ()),
                sender_state=states.get(m.sender_id) if m.sender_id is not None else None,
            )
            for m in messages
        ]
        reads = self._reads.last_reads_for_channel(channel)
        members = [
            MemberDto(id=m.id, name=m.name, last_read_at=reads.get(m.id), state=states.get(m.id)) for m in member_infos
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
    ) -> None:
        # Public: the push stack is constructed after the chat use cases in create_app(),
        # so it is attached afterwards rather than passed in here.
        self._directory = directory
        self._messages = message_repo
        self._reads = read_repo
        self._storage = storage
        self._db = db_session
        self.notifier = notifier

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

        ``lang`` is accepted for backward compatibility with older clients and ignored.

        ``reply_to_id``, when given, must name a message of this same channel (else
        ``ReplyTargetNotInChannelError``).

        Raises:
            ChatChannelNotFoundError, NotChannelMemberError, EmptyMessageError,
            UnsupportedAttachmentTypeError, AttachmentTooLargeError, ReplyTargetNotInChannelError.
        """
        channel = _parse_channel(channel_key)
        _require_member(self._directory, actor_id, channel)

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
            real_type = _sniffed_attachment_type(content_type, data)
            if real_type is None:
                raise AttachmentContentMismatchError(f"The file is not a valid '{content_type}'.")
            content_type = real_type
            stored = ChatAttachment(
                storage_key="",  # set once the message id is known
                filename=strip_control_chars(filename)[:255] or "attachment",
                content_type=content_type,
                size_bytes=len(data),
            )

        try:
            message = ChatMessage.create(
                channel=channel,
                sender_id=actor_id,
                body=body,
                attachment=stored,
                reply_to_id=reply_to_id,
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
            )

        self._messages.add(message)
        # Sending implies having seen the channel up to now.
        self._reads.mark_read(actor_id, channel, message.created_at)
        self._db.commit()
        # After the commit: a push must never be able to roll back the message.
        if self.notifier is not None:
            self.notifier.message_sent(
                channel=channel,
                sender_id=actor_id,
                preview=message.body,
                sent_at=message.created_at,
                attachment_type=stored.content_type if stored is not None else None,
            )
        names = self._directory.display_names([actor_id])
        return MessageDto.from_entity(message, names.get(actor_id, "?"))


class SetMessageReactionUseCase:
    """Leave an emoji on a message, or change the one already left (one per person)."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        reaction_repo: ChatReactionRepositoryPort,
        db_session: TransactionalSessionPort,
        safety_repo: Optional[ChatSafetyRepositoryPort] = None,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._reactions = reaction_repo
        self._db = db_session
        self._safety = safety_repo

    def _visible_reactions(self, actor_id: UUID, message_id: UUID) -> tuple[ReactionDto, ...]:
        """The message's reactions as the actor sees them in the thread (blocked people hidden)."""
        blocked = self._safety.blocked_ids(actor_id) if self._safety is not None else set()
        reactions = self._reactions.reactions_for_messages([message_id])
        return _reactions_with_names(self._directory, reactions, blocked).get(message_id, ())

    def execute(self, *, actor_id: UUID, message_id: UUID, emoji: str) -> tuple[ReactionDto, ...]:
        """Returns every reaction of the message after the change.

        Raises:
            ChatMessageNotFoundError, ChatChannelNotFoundError, NotChannelMemberError, InvalidReactionError.
        """
        if emoji not in REACTION_EMOJIS:
            raise InvalidReactionError(f"'{emoji}' is not an accepted reaction.")
        message = self._messages.find_by_id(message_id)
        if message is None:
            raise ChatMessageNotFoundError(f"Message {message_id} not found.")
        _require_member(self._directory, actor_id, message.channel)
        self._reactions.set_reaction(message_id, actor_id, emoji)
        self._db.commit()
        return self._visible_reactions(actor_id, message_id)


class RemoveMessageReactionUseCase:
    """Take the actor's emoji off a message. Idempotent."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        reaction_repo: ChatReactionRepositoryPort,
        db_session: TransactionalSessionPort,
        safety_repo: Optional[ChatSafetyRepositoryPort] = None,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._reactions = reaction_repo
        self._db = db_session
        self._safety = safety_repo

    def _visible_reactions(self, actor_id: UUID, message_id: UUID) -> tuple[ReactionDto, ...]:
        """The message's reactions as the actor sees them in the thread (blocked people hidden)."""
        blocked = self._safety.blocked_ids(actor_id) if self._safety is not None else set()
        reactions = self._reactions.reactions_for_messages([message_id])
        return _reactions_with_names(self._directory, reactions, blocked).get(message_id, ())

    def execute(self, *, actor_id: UUID, message_id: UUID) -> tuple[ReactionDto, ...]:
        message = self._messages.find_by_id(message_id)
        if message is None:
            raise ChatMessageNotFoundError(f"Message {message_id} not found.")
        _require_member(self._directory, actor_id, message.channel)
        self._reactions.remove_reaction(message_id, actor_id)
        self._db.commit()
        return self._visible_reactions(actor_id, message_id)


class MarkChannelReadUseCase:
    """Move the actor's read marker of a channel to now."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        read_repo: ChatReadRepositoryPort,
        db_session: TransactionalSessionPort,
        bell: Any = None,
    ) -> None:
        self._directory = directory
        self._reads = read_repo
        self._db = db_session
        # Public, attached after the push stack exists: ``channel_read(channel_key, user_id,
        # read_at)`` clears the bell's chat entries of the channel just read.
        self.bell = bell

    def execute(self, *, actor_id: UUID, channel_key: str) -> None:
        channel = _parse_channel(channel_key)
        _require_member(self._directory, actor_id, channel)
        now = datetime.now(timezone.utc)
        self._reads.mark_read(actor_id, channel, now)
        self._db.commit()
        if self.bell is not None:
            self.bell.channel_read(channel_key=channel.key, user_id=actor_id, read_at=now)


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


class ReportMessageUseCase:
    """A channel member reports someone else's message; the company's admins are told.

    The admins review open reports in the app (``ListReportsUseCase``) and either remove the
    message for everyone or dismiss the report (``ResolveReportUseCase``)."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        safety_repo: ChatSafetyRepositoryPort,
        db_session: TransactionalSessionPort,
        notifier: Any = None,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._safety = safety_repo
        self._db = db_session
        # Public, attached after the push stack is built (like SendMessageUseCase.notifier).
        self.notifier = notifier

    def execute(self, *, actor_id: UUID, message_id: UUID, reason: Optional[str] = None) -> None:
        """Raises ChatMessageNotFoundError, ChatChannelNotFoundError, NotChannelMemberError,
        CannotReportOwnMessageError."""
        message = self._messages.find_by_id(message_id)
        if message is None:
            raise ChatMessageNotFoundError(f"Message {message_id} not found.")
        _require_member(self._directory, actor_id, message.channel)
        if message.sender_id == actor_id:
            raise CannotReportOwnMessageError("You cannot report your own message.")
        cleaned = (reason or "").strip()[:500] or None
        created = self._safety.add_report(message_id, actor_id, cleaned)
        self._db.commit()
        logger.warning("chat message reported message=%s reporter=%s reason=%r", message_id, actor_id, cleaned)
        if created is not False and self.notifier is not None:
            company_id = self._directory.channel_company_id(message.channel)
            if company_id is not None:
                # After the commit: a failed push must never lose the report.
                self.notifier.message_reported(
                    company_id=company_id,
                    channel=message.channel,
                    message=message,
                    reporter_id=actor_id,
                    reason=cleaned,
                )


def _require_company_admin(directory: ChatDirectoryPort, actor_id: UUID, company_id: UUID) -> None:
    if actor_id not in directory.company_admin_ids(company_id):
        raise NotCompanyAdminError("Only the company's admins review chat reports.")


class ListReportsUseCase:
    """Open reports on the messages of a company's channels, for that company's admins."""

    def __init__(self, directory: ChatDirectoryPort, safety_repo: ChatSafetyRepositoryPort) -> None:
        self._directory = directory
        self._safety = safety_repo

    def execute(self, *, actor_id: UUID, company_id: UUID) -> list[ReportDto]:
        _require_company_admin(self._directory, actor_id, company_id)
        reports = self._safety.list_open_reports(self._directory.company_channels(company_id))
        people = list(
            {*(r.reporter_id for r in reports), *(r.message.sender_id for r in reports if r.message.sender_id)}
        )
        names = self._directory.display_names(people)
        states = _account_states(self._directory, people)
        channel_names: dict[str, str] = {}
        result: list[ReportDto] = []
        for r in reports:
            key = r.message.channel.key
            if key not in channel_names:
                channel_names[key] = self._directory.channel_name(r.message.channel)
            sender = r.message.sender_id
            result.append(
                ReportDto(
                    id=r.id,
                    message=replace(
                        MessageDto.from_entity(r.message, names.get(sender, "?") if sender is not None else "Folio"),
                        sender_state=states.get(sender) if sender is not None else None,
                    ),
                    channel_name=channel_names[key],
                    reporter_id=r.reporter_id,
                    reporter_name=names.get(r.reporter_id, "?"),
                    reason=r.reason,
                    created_at=r.created_at,
                )
            )
        return result


class ResolveReportUseCase:
    """A company admin closes a report: ``"dismiss"`` keeps the message, ``"delete_message"``
    removes it (and its attachment) for everyone. Either way every open report of that message
    is closed, since one decision answers them all."""

    ACTIONS = ("dismiss", "delete_message")

    def __init__(
        self,
        directory: ChatDirectoryPort,
        message_repo: ChatMessageRepositoryPort,
        safety_repo: ChatSafetyRepositoryPort,
        storage: ChatAttachmentStoragePort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._directory = directory
        self._messages = message_repo
        self._safety = safety_repo
        self._storage = storage
        self._db = db_session

    def execute(self, *, actor_id: UUID, report_id: UUID, action: str) -> None:
        """Raises ChatReportNotFoundError, NotCompanyAdminError, ValueError (unknown action)."""
        if action not in self.ACTIONS:
            raise ValueError(f"Unknown action '{action}'.")
        report = self._safety.find_report(report_id)
        if report is None:
            raise ChatReportNotFoundError(f"Report {report_id} not found.")
        company_id = self._directory.channel_company_id(report.message.channel)
        if company_id is None or actor_id not in self._directory.company_admin_ids(company_id):
            # Not this admin's company: as if the report did not exist.
            raise ChatReportNotFoundError(f"Report {report_id} not found.")
        self._safety.resolve_reports_of_message(report.message.id, datetime.now(timezone.utc))
        attachment = report.message.attachment
        if action == "delete_message":
            self._messages.delete_message(report.message.id)
        self._db.commit()
        logger.warning(
            "chat report resolved report=%s message=%s action=%s by=%s", report_id, report.message.id, action, actor_id
        )
        if action == "delete_message" and attachment is not None and attachment.storage_key:
            try:
                self._storage.delete(attachment.storage_key)
            except Exception:  # the message is gone; an orphan object is harmless
                logger.exception("chat attachment delete failed key=%s", attachment.storage_key)


class BlockUserUseCase:
    """Hide a person's chat messages (and pushes) from the actor. Idempotent."""

    def __init__(
        self,
        directory: ChatDirectoryPort,
        safety_repo: ChatSafetyRepositoryPort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._directory = directory
        self._safety = safety_repo
        self._db = db_session

    def execute(self, *, actor_id: UUID, target_id: UUID) -> None:
        if target_id == actor_id:
            raise CannotBlockSelfError("You cannot block yourself.")
        if not self._directory.shares_channel(actor_id, target_id):
            raise BlockTargetNotFoundError("No such user in your channels.")
        self._safety.block(actor_id, target_id)
        self._db.commit()


class UnblockUserUseCase:
    def __init__(self, safety_repo: ChatSafetyRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._safety = safety_repo
        self._db = db_session

    def execute(self, *, actor_id: UUID, target_id: UUID) -> None:
        self._safety.unblock(actor_id, target_id)
        self._db.commit()


class ListBlockedUsersUseCase:
    def __init__(self, directory: ChatDirectoryPort, safety_repo: ChatSafetyRepositoryPort) -> None:
        self._directory = directory
        self._safety = safety_repo

    def execute(self, *, actor_id: UUID) -> list[MemberDto]:
        ids = list(self._safety.blocked_ids(actor_id))
        names = self._directory.display_names(ids)
        return sorted((MemberDto(id=i, name=names.get(i, "?")) for i in ids), key=lambda m: m.name.lower())


__all__ = [
    "BlockUserUseCase",
    "ListReportsUseCase",
    "ResolveReportUseCase",
    "ListBlockedUsersUseCase",
    "RemoveMessageReactionUseCase",
    "ReportMessageUseCase",
    "SetMessageReactionUseCase",
    "UnblockUserUseCase",
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
