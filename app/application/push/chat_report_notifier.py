"""Tell a company's admins that a chat message was reported.

A report is a request for a decision, so it goes to the people who can take it: the admins of
the company that owns the channel, in their bell and as a push (admin family, like the other
admin events). The reporter and the reported author are never told through this path.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Protocol
from uuid import UUID

from app.application.push.dispatcher import PushDispatcher
from app.domain.entities.chat_message import ChannelRef, ChatMessage
from app.domain.notifications.categories import NotificationCategory

logger = logging.getLogger(__name__)

_MAX_PREVIEW = 120

_TITLE = {"vi": "Tin nhắn bị báo cáo", "fr": "Message signalé", "en": "Message reported"}
_BODY = {
    "vi": "{reporter} đã báo cáo tin nhắn của {author} · {channel}: “{preview}”",
    "fr": "{reporter} a signalé un message de {author} · {channel} : « {preview} »",
    "en": "{reporter} reported a message from {author} · {channel}: “{preview}”",
}
_REASON = {"vi": " — Lý do: {reason}", "fr": " — Motif : {reason}", "en": " — Reason: {reason}"}
_NO_TEXT = {"vi": "tệp đính kèm", "fr": "pièce jointe", "en": "attachment"}


class ReportDirectory(Protocol):
    def company_admin_ids(self, company_id: UUID) -> list[UUID]: ...
    def display_names(self, user_ids: list[UUID]) -> Dict[UUID, str]: ...
    def channel_name(self, channel: ChannelRef) -> str: ...


class ChatReportNotifier:
    def __init__(self, dispatcher: PushDispatcher, directory: ReportDirectory) -> None:
        self._dispatcher = dispatcher
        self._directory = directory

    def message_reported(
        self,
        *,
        company_id: UUID,
        channel: ChannelRef,
        message: ChatMessage,
        reporter_id: UUID,
        reason: Optional[str],
    ) -> None:
        """Never raises: the report is already stored."""
        try:
            admins = [
                uid
                for uid in self._directory.company_admin_ids(company_id)
                if uid not in (reporter_id, message.sender_id)
            ]
            if not admins:
                return
            ids = [reporter_id] + ([message.sender_id] if message.sender_id is not None else [])
            names = self._directory.display_names(ids)
            reporter = names.get(reporter_id, "?")
            author = names.get(message.sender_id, "?") if message.sender_id is not None else "Folio"
            channel_name = self._directory.channel_name(channel)
            preview = (message.body or "").strip().replace("\n", " ")[:_MAX_PREVIEW]

            def render(locale: str) -> tuple[str, str]:
                lang = locale if locale in _TITLE else "en"
                body = _BODY[lang].format(
                    reporter=reporter, author=author, channel=channel_name, preview=preview or _NO_TEXT[lang]
                )
                if reason:
                    body += _REASON[lang].format(reason=reason)
                return _TITLE[lang], body

            self._dispatcher.dispatch(
                category=NotificationCategory.MEMBERSHIP.value,
                recipients=admins,
                render=render,
                data={
                    "kind": "chat_report",
                    "company_id": str(company_id),
                    "channel_key": channel.key,
                    "message_id": str(message.id),
                },
            )
        except Exception:
            logger.exception("chat report notification failed message=%s", message.id)
