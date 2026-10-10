"""Push notifications for chat messages.

Chat is the noisiest source in the product, so delivery is coalesced per (user, channel):
a member is pushed at most once per quiet window. The body carries the newest message and,
when the user is already behind, how many they have not read — so one push after a burst
still tells them the size of what they missed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Protocol
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
_MORE = {
    "vi": "{preview} (+{count} tin nhắn chưa đọc)",
    "fr": "{preview} (+{count} messages non lus)",
    "en": "{preview} (+{count} unread)",
}
_IMAGE_ONLY = {"vi": "Đã gửi một ảnh", "fr": "A envoyé une image", "en": "Sent an image"}


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
    ) -> None:
        self._dispatcher = dispatcher
        self._directory = directory
        self._markers = markers
        self._reads = reads
        self._messages = messages
        self._names = names
        self._window = window_seconds

    def message_sent(self, *, channel: ChannelRef, sender_id: UUID, preview: str | None, sent_at: datetime) -> None:
        """Notify the channel's other members, at most once per window each."""
        try:
            members = [m.id for m in self._directory.list_members(channel) if m.id != sender_id]
            if not members:
                return
            due = self._markers.due_recipients(members, channel.key, self._window, sent_at)
            if not due:
                return

            locale = self._dispatcher.locale
            sender_name = self._directory.display_names([sender_id]).get(sender_id, "?")
            channel_name = self._names.channel_name(channel)
            text = (preview or "").strip().replace("\n", " ")[:_MAX_PREVIEW] or _IMAGE_ONLY[locale]
            title = _TEXT[locale][0].format(sender=sender_name, channel=channel_name)

            # A per-user unread count means a per-user body, so dispatch one group at a time.
            last_reads = self._reads.last_reads_for_channel(channel)
            for user_id in due:
                unread = self._messages.count_since(channel, last_reads.get(user_id), user_id)
                body = (
                    _MORE[locale].format(preview=text, count=unread - 1)
                    if unread > 1
                    else _TEXT[locale][1].format(preview=text)
                )
                self._dispatcher.dispatch(
                    category=NotificationCategory.CHAT.value,
                    recipients=[user_id],
                    title=title,
                    body=body,
                    data={"kind": "chat_message", "channel_key": channel.key},
                )
            self._markers.mark_notified(due, channel.key, sent_at)
        except Exception:  # a chat push must never break sending the message
            logger.exception("chat push failed channel=%s", channel.key)
