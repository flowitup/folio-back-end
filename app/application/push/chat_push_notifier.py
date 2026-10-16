"""Push notifications for chat messages.

Chat is the noisiest source in the product, so delivery is coalesced per (user, channel):
a member is pushed at most once per quiet window. The body carries the newest message and,
when the user is already behind, how many they have not read — so one push after a burst
still tells them the size of what they missed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID

from app.application.push.dispatcher import PushDispatcher
from app.domain.entities.chat_message import ChannelRef
from app.domain.notifications.categories import NotificationCategory

logger = logging.getLogger(__name__)

# One push per member per channel per window. Long enough to fold a back-and-forth into a
# single notification, short enough that a reply hours later still reaches the recipient.
QUIET_WINDOW_SECONDS = 60

_MAX_PREVIEW = 120

# (title, body) where body is the message preview; the count line is appended separately.
_TEXT: Dict[str, tuple] = {
    "vi": ("{sender} · {channel}", "{preview}"),
    "fr": ("{sender} · {channel}", "{preview}"),
    "en": ("{sender} · {channel}", "{preview}"),
}
# "(+N unread)": French needs the singular for one ("+1 message non lu").
_MORE_ONE = {
    "vi": "{preview} (+1 tin nhắn chưa đọc)",
    "fr": "{preview} (+1 message non lu)",
    "en": "{preview} (+1 unread)",
}
_MORE = {
    "vi": "{preview} (+{count} tin nhắn chưa đọc)",
    "fr": "{preview} (+{count} messages non lus)",
    "en": "{preview} (+{count} unread)",
}
_ADMIN_LABEL = {"vi": "Quản trị", "fr": "Administration", "en": "Admin"}
_IMAGE_ONLY = {"vi": "Đã gửi một ảnh", "fr": "A envoyé une image", "en": "Sent an image"}
_VOICE_ONLY = {"vi": "Đã gửi một tin nhắn thoại", "fr": "A envoyé un message vocal", "en": "Sent a voice message"}
_FILE_ONLY = {"vi": "Đã gửi một tệp", "fr": "A envoyé un fichier", "en": "Sent a file"}


def _attachment_text(attachment_type: Optional[str], locale: str) -> str:
    """What a message without text sent: a voice note, a photo or another file."""
    kind = (attachment_type or "").lower()
    if kind.startswith("audio/"):
        return _VOICE_ONLY[locale]
    if kind.startswith("image/") or not kind:
        return _IMAGE_ONLY[locale]
    return _FILE_ONLY[locale]


class ChatEventMarker(Protocol):
    def mark_read_matching(self, *, kinds, match, user_ids=None, before=None) -> int: ...


class ChatPushMarkerPort(Protocol):
    def due_recipients(
        self, user_ids: List[UUID], channel_key: str, window_seconds: int, now: datetime
    ) -> List[UUID]: ...
    def mark_notified(self, user_ids: List[UUID], channel_key: str, now: datetime) -> None: ...


class ChatDirectoryReader(Protocol):
    def list_members(self, channel: ChannelRef) -> list: ...
    def display_names(self, user_ids: list) -> Dict[UUID, str]: ...


class ChatUnreadReader(Protocol):
    def last_reads_for_channel(self, channel: ChannelRef) -> Dict[UUID, datetime]: ...
    def count_since(self, channel: ChannelRef, since: Optional[datetime], exclude_sender: UUID) -> int: ...


class ChannelNameReader(Protocol):
    def channel_name(self, channel: ChannelRef) -> str: ...


class ChatPushNotifier:
    def __init__(
        self,
        dispatcher: PushDispatcher,
        directory: ChatDirectoryReader,
        markers: ChatPushMarkerPort,
        reads: ChatUnreadReader,
        messages: ChatUnreadReader,
        names: ChannelNameReader,
        window_seconds: int = QUIET_WINDOW_SECONDS,
        blocks: Any = None,
        events: Optional[ChatEventMarker] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._directory = directory
        self._markers = markers
        self._reads = reads
        self._messages = messages
        self._names = names
        self._window = window_seconds
        # Optional ``blocker_ids_of(user_id)``: people who blocked the sender are not pushed.
        self._blocks = blocks
        # Optional bell store: chat entries of a channel are read once the channel is read.
        self._events = events

    def channel_read(self, *, channel_key: str, user_id: UUID, read_at: datetime) -> None:
        """``user_id`` has seen ``channel_key`` up to ``read_at``: its bell chat entries are read too,
        so the bell does not count again what the chat button already cleared. Never raises."""
        if self._events is None:
            return
        try:
            self._events.mark_read_matching(
                kinds=("chat_message",), match={"channel_key": channel_key}, user_ids=[user_id], before=read_at
            )
        except Exception:
            logger.exception("chat bell mark-read failed channel=%s", channel_key)

    def _unread_since(self, channel: ChannelRef, user_id: UUID, last_read: Optional[datetime]) -> int:
        """Unread count for one member; with no read marker, count from when they joined (when known)."""
        if last_read is None:
            joined_at = getattr(self._directory, "joined_at", None)
            if joined_at is not None:
                try:
                    last_read = joined_at(user_id, channel)
                except Exception:
                    last_read = None
        return self._messages.count_since(channel, last_read, user_id)

    def message_sent(
        self,
        *,
        channel: ChannelRef,
        sender_id: UUID,
        preview: str | None,
        sent_at: datetime,
        attachment_type: Optional[str] = None,
    ) -> None:
        """Notify the channel's other members, at most once per window each.

        ``attachment_type`` (the attachment's content type) names what a message without text
        sent: a voice note says so instead of "sent an image"."""
        # Sending means the sender has seen the channel: their own chat entries are read.
        self.channel_read(channel_key=channel.key, user_id=sender_id, read_at=sent_at)
        try:
            blockers = self._blocks.blocker_ids_of(sender_id) if self._blocks is not None else set()
            members = [
                m.id for m in self._directory.list_members(channel) if m.id != sender_id and m.id not in blockers
            ]
            if not members:
                return
            due = self._markers.due_recipients(members, channel.key, self._window, sent_at)
            if not due:
                return

            sender_name = self._directory.display_names([sender_id]).get(sender_id, "?")
            channel_name = self._names.channel_name(channel)
            preview_text = (preview or "").strip().replace("\n", " ")[:_MAX_PREVIEW]

            # A per-user unread count means a per-user body, so dispatch one group at a time.
            last_reads = self._reads.last_reads_for_channel(channel)
            data = {"kind": "chat_message", "channel_key": channel.key, "actor_id": str(sender_id)}
            if channel.kind == "project":
                # Lets the bell hide the entry once the reader is no longer on the project.
                data["project_id"] = str(channel.id)
            elif channel.kind == "company":
                data["company_id"] = str(channel.id)
            for user_id in due:
                unread = self._unread_since(channel, user_id, last_reads.get(user_id))

                def render(locale: str, unread: int = unread) -> tuple[str, str]:
                    text = preview_text or _attachment_text(attachment_type, locale)
                    # The admin channel shares its company's name; say which one this is.
                    name = f"{_ADMIN_LABEL[locale]} · {channel_name}" if channel.kind == "admin" else channel_name
                    title = _TEXT[locale][0].format(sender=sender_name, channel=name)
                    if unread == 2:
                        return title, _MORE_ONE[locale].format(preview=text)
                    if unread > 2:
                        return title, _MORE[locale].format(preview=text, count=unread - 1)
                    return title, _TEXT[locale][1].format(preview=text)

                self._dispatcher.dispatch(
                    category=NotificationCategory.CHAT.value,
                    recipients=[user_id],
                    render=render,
                    data=dict(data),
                )
            self._markers.mark_notified(due, channel.key, sent_at)
        except Exception:  # a chat push must never break sending the message
            logger.exception("chat push failed channel=%s", channel.key)
