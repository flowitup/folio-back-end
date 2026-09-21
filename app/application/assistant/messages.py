"""AssistantMessenger — posts assistant-authored replies into a user's assistant channel.

Every post builds a ``ChatMessage.assistant(...)`` with a plain-text ``body`` fallback (so
the web widget and an older app build that does not understand a ``content_type`` still
show something useful), persists it through the chat message repository, commits, and
then — best-effort — pushes the owner through the chat push notifier's assistant-safe
path (``ChatPushNotifier.message_sent_by_assistant``, which does not exclude the message's
own "sender" the way a human-to-human push would).
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.application.assistant.ports import MessagePosterPort
from app.application.invitations.ports import TransactionalSessionPort
from app.domain.entities.chat_message import ChannelRef, ChatMessage

# Keeps the text fallback of a choice message readable even if a caller ever passes an
# unreasonably long option list.
_MAX_OPTIONS_IN_FALLBACK = 20


def _card_fallback(card: dict[str, Any]) -> str:
    title = str(card.get("title") or "").strip()
    subtitle = card.get("subtitle")
    if subtitle:
        return f"{title} – {subtitle}"
    return title


def _choice_fallback(prompt: str, options: list[dict[str, Any]]) -> str:
    lines = [prompt.strip()] if prompt.strip() else []
    for index, option in enumerate(options[:_MAX_OPTIONS_IN_FALLBACK], start=1):
        label = option.get("label") if isinstance(option, dict) else None
        if label:
            lines.append(f"{index}. {label}")
    return "\n".join(lines)


class AssistantMessenger:
    """Posts text/card/choice/job_status replies into ``assistant:<user_id>``.

    ``notifier`` (a ``ChatPushNotifier``) is a public attribute rather than a
    constructor-only argument because the push stack is wired after the assistant
    bounded context in ``create_app()`` — mirrors ``SendMessageUseCase.notifier``.
    """

    def __init__(self, message_repo: MessagePosterPort, db_session: TransactionalSessionPort) -> None:
        self._messages = message_repo
        self._db = db_session
        self.notifier: Optional[Any] = None

    def _post(
        self,
        *,
        user_id: UUID,
        content_type: str,
        payload: dict[str, Any] | None,
        body: str,
        reply_to_id: UUID | None,
        trace_id: str | None,
    ) -> ChatMessage:
        channel = ChannelRef(kind="assistant", id=user_id)
        message = ChatMessage.assistant(
            channel=channel,
            content_type=content_type,
            payload=payload,
            body=body,
            reply_to_id=reply_to_id,
            trace_id=trace_id,
        )
        self._messages.add(message)
        self._db.commit()
        # After the commit: a push must never be able to roll back the message.
        if self.notifier is not None:
            self.notifier.message_sent_by_assistant(channel=channel, preview=message.body, sent_at=message.created_at)
        return message

    def post_text(
        self, user_id: UUID, body: str, *, reply_to_id: UUID | None = None, trace_id: str | None = None
    ) -> ChatMessage:
        return self._post(
            user_id=user_id, content_type="text", payload=None, body=body, reply_to_id=reply_to_id, trace_id=trace_id
        )

    def post_card(
        self, user_id: UUID, card: dict[str, Any], *, reply_to_id: UUID | None = None, trace_id: str | None = None
    ) -> ChatMessage:
        return self._post(
            user_id=user_id,
            content_type="card",
            payload={"card": card},
            body=_card_fallback(card),
            reply_to_id=reply_to_id,
            trace_id=trace_id,
        )

    def post_choice(
        self,
        user_id: UUID,
        prompt: str,
        options: list[dict[str, Any]],
        *,
        reply_to_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> ChatMessage:
        payload = {"prompt": prompt, "options": options, "answered": None}
        return self._post(
            user_id=user_id,
            content_type="choice",
            payload=payload,
            body=_choice_fallback(prompt, options),
            reply_to_id=reply_to_id,
            trace_id=trace_id,
        )

    def post_job_status(
        self,
        user_id: UUID,
        job_id: str,
        state: str,
        text: str,
        *,
        progress: float | None = None,
        reply_to_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> ChatMessage:
        payload = {"job_id": job_id, "state": state, "text": text, "progress": progress}
        return self._post(
            user_id=user_id,
            content_type="job_status",
            payload=payload,
            body=text,
            reply_to_id=reply_to_id,
            trace_id=trace_id,
        )
