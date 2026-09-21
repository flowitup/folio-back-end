"""AssistantMessenger — posts assistant-authored replies into a chat channel.

Every post builds a ``ChatMessage.assistant(...)`` with a plain-text ``body`` fallback (so
the web widget and an older app build that does not understand a ``content_type`` still
show something useful), persists it through the chat message repository, commits, and
then — best-effort — pushes the channel through the chat push notifier's assistant-safe
path (``ChatPushNotifier.message_sent_by_assistant``, which does not exclude the message's
own "sender" the way a human-to-human push would).

Every ``post_*`` method takes an explicit ``channel`` — the message's own channel
(company/project/admin) when called from ``AssistantService`` or a feature under
``app.application.assistant.features`` (ticket/material/invoice-fetch/labor/tasks/
admin-answers), all of which are channel-aware since phase 03/04. ``channel`` omitted
falls back to the retired ``assistant:<user_id>`` channel — kept only so an internal
caller with no channel context (none remain in production code) still compiles.

Every ``post_*`` method also takes a REQUIRED ``scope`` keyword (``ChannelScope | None``
— required so a missing call site is a type error, not a silent bypass; ``None`` itself
still fails closed, see ``scope.redact``) — ``_post`` runs the payload through
``app.application.assistant.scope.redact`` before persisting it (D17 layer 1): a card/
choice/job_status built for a non-admin channel never carries a ``finance_company``/
``payroll`` classified field, whichever feature posted it. The plain-text ``body``
fallback is always built from the REDACTED payload (never the raw one passed in), so a
stripped field can never survive verbatim in the one field every client (and the push
notification preview) always reads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from app.application.assistant.exceptions import AssistantError
from app.application.assistant.models import ChannelScope
from app.application.assistant.ports import MessagePosterPort
from app.application.assistant.scope import redact
from app.application.invitations.ports import TransactionalSessionPort
from app.domain.entities.chat_message import ChannelRef, ChatMessage

# Keeps the text fallback of a choice message readable even if a caller ever passes an
# unreasonably long option list.
_MAX_OPTIONS_IN_FALLBACK = 20


#: The card content-type's wire contract (phase 01, shared with the mobile/web apps):
#: ``{"card": {"type", "id", "project_id", "title", "subtitle", "badge", "thumbnail_url",
#: "extra"}}``. The app's parser only recognises this exact shape — never post a card
#: with any other top-level keys, and never an absolute ``thumbnail_url`` (the app
#: attaches its bearer token to a relative ``/api/...`` path only).
CARD_TYPES = ("invoice", "material")


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
        reply_to_id: UUID | None,
        trace_id: str | None,
        channel: ChannelRef | None,
        scope: ChannelScope | None,
        body: str | None = None,
        body_builder: "Callable[[dict[str, Any] | None], str] | None" = None,
    ) -> ChatMessage:
        target = channel if channel is not None else ChannelRef(kind="assistant", id=user_id)
        # D17 layer 1: strip every confidential-class field the scope does not allow
        # before the message is ever built/persisted — the ONE place every card/choice/
        # job_status payload passes through, whichever feature/service call site posted
        # it. `body` is then derived from the REDACTED payload (via `body_builder`) when
        # the caller has one — never from the raw `payload` — so a stripped field cannot
        # resurface verbatim in the plain-text fallback every client (and the push
        # preview) reads (review finding H1(b)).
        redacted_payload = redact(scope, payload)
        resolved_body = body_builder(redacted_payload) if body_builder is not None else (body or "")
        message = ChatMessage.assistant(
            channel=target,
            content_type=content_type,
            payload=redacted_payload,
            body=resolved_body,
            reply_to_id=reply_to_id,
            trace_id=trace_id,
        )
        self._messages.add(message)
        self._db.commit()
        # After the commit: a push must never be able to roll back the message.
        if self.notifier is not None:
            self.notifier.message_sent_by_assistant(channel=target, preview=message.body, sent_at=message.created_at)
        return message

    def post_text(
        self,
        user_id: UUID,
        body: str,
        *,
        scope: ChannelScope | None,
        reply_to_id: UUID | None = None,
        trace_id: str | None = None,
        channel: ChannelRef | None = None,
    ) -> ChatMessage:
        return self._post(
            user_id=user_id,
            content_type="text",
            payload=None,
            body=body,
            reply_to_id=reply_to_id,
            trace_id=trace_id,
            channel=channel,
            scope=scope,
        )

    def post_card(
        self,
        user_id: UUID,
        *,
        card_type: str,
        entity_id: UUID,
        title: str,
        scope: ChannelScope | None,
        subtitle: str | None = None,
        badge: str | None = None,
        project_id: UUID | None = None,
        thumbnail_url: str | None = None,
        extra: dict[str, Any] | None = None,
        reply_to_id: UUID | None = None,
        trace_id: str | None = None,
        channel: ChannelRef | None = None,
    ) -> ChatMessage:
        """Builds the ``card`` content-type's wire contract itself — callers pass typed
        fields, never a raw dict, so every card the assistant posts is shaped identically
        for the app's parser (see ``CARD_TYPES``/the module docstring above).

        ``thumbnail_url``, when set, must be a relative ``/api/...`` path (the app
        attaches its own bearer token); an absolute URL would be rejected client-side.
        """
        if card_type not in CARD_TYPES:
            raise AssistantError(f"Unknown card_type {card_type!r}, expected one of {CARD_TYPES}.")
        if thumbnail_url is not None and not thumbnail_url.startswith("/api/"):
            raise AssistantError("thumbnail_url must be a relative '/api/...' path.")
        card: dict[str, Any] = {
            "type": card_type,
            "id": str(entity_id),
            "project_id": str(project_id) if project_id is not None else None,
            "title": title,
            "subtitle": subtitle,
            "badge": badge,
            "thumbnail_url": thumbnail_url,
            "extra": extra or {},
        }
        return self._post(
            user_id=user_id,
            content_type="card",
            payload={"card": card},
            # Fallback built from the REDACTED payload (H1(b)): a stripped `extra` field
            # must not resurface verbatim in the subtitle/body every client renders.
            body_builder=lambda redacted: _card_fallback((redacted or {}).get("card") or {}),
            reply_to_id=reply_to_id,
            trace_id=trace_id,
            channel=channel,
            scope=scope,
        )

    def post_choice(
        self,
        user_id: UUID,
        prompt: str,
        options: list[dict[str, Any]],
        *,
        scope: ChannelScope | None,
        reply_to_id: UUID | None = None,
        trace_id: str | None = None,
        channel: ChannelRef | None = None,
        addressed_to: UUID | None = None,
    ) -> ChatMessage:
        """``addressed_to`` (the asker) defaults to ``user_id`` — every current caller
        already passes the asker's id there, so this is free for them. Only the person
        it names may answer the choice (``SubmitAssistantActionUseCase``)."""
        payload = {
            "prompt": prompt,
            "options": options,
            "answered": None,
            "addressed_to": str(addressed_to if addressed_to is not None else user_id),
        }
        return self._post(
            user_id=user_id,
            content_type="choice",
            payload=payload,
            # Same H1(b) fix as post_card: build the fallback from whatever survives
            # redaction in `options`, never from the caller's original list.
            body_builder=lambda redacted: _choice_fallback(prompt, (redacted or {}).get("options") or []),
            reply_to_id=reply_to_id,
            trace_id=trace_id,
            channel=channel,
            scope=scope,
        )

    def post_job_status(
        self,
        user_id: UUID,
        job_id: str,
        state: str,
        text: str,
        *,
        scope: ChannelScope | None,
        progress: float | None = None,
        reply_to_id: UUID | None = None,
        trace_id: str | None = None,
        channel: ChannelRef | None = None,
    ) -> ChatMessage:
        payload = {"job_id": job_id, "state": state, "text": text, "progress": progress}
        return self._post(
            user_id=user_id,
            content_type="job_status",
            payload=payload,
            body=text,
            reply_to_id=reply_to_id,
            trace_id=trace_id,
            channel=channel,
            scope=scope,
        )

    def update_job_status(
        self,
        message_id: UUID,
        *,
        state: str,
        text: str,
        progress: float | None = None,
        terminal: bool = False,
    ) -> None:
        """Update a ``job_status`` message's payload in place (feature B's state machine
        — queued -> running -> not_ready|blocked|done|failed|not_found). Pushes through
        the notifier only when ``terminal`` — the plan's "job_status shows a spinner
        while queued|running" only needs the app's 5s poll to see intermediate states.
        """
        message = self._messages.find_by_id(message_id)
        if message is None:
            return
        payload = dict(message.payload or {})
        payload["state"] = state
        payload["text"] = text
        payload["progress"] = progress
        self._messages.update_payload(message_id, payload)
        self._db.commit()
        # After the commit: a push must never be able to roll back the update.
        if terminal and self.notifier is not None:
            self.notifier.message_sent_by_assistant(
                channel=message.channel, preview=text, sent_at=datetime.now(timezone.utc)
            )
