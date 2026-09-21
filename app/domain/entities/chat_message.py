"""ChatMessage domain entity — one line of team chat in a company, project or admin channel.

Channels are virtual: a message belongs to ``(channel_kind, channel_id)`` where
``channel_kind`` is ``"company"`` (every company member), ``"project"`` (one channel per
project) or ``"admin"`` (one per-company channel for that company's admins and platform
ops, keyed by the company's id). Membership is derived from company access rows and
project memberships at read time — there is no channel table.

There used to be a fourth kind, ``"assistant"`` (a private per-user conversation with the
Folio Assistant): it is retired — ``ChannelRef.parse`` rejects it (every route then
answers 404, same as any other unknown channel) and old ``chat_messages`` rows with
``channel_kind = 'assistant'`` are left untouched in the database, simply unreachable.
The assistant now lives inside the company/project/admin channels above, addressed with
an ``@folio`` mention (see ``mentions_assistant_token``) or a reply to one of its
messages (``sender_type == "assistant"``).

A message authored by the assistant itself has ``sender_id`` ``None`` (``sender_type ==
"assistant"``); every other message is authored by a user and carries the actor's id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

CHANNEL_KINDS: frozenset[str] = frozenset({"company", "project", "admin"})

#: Detects an ``@folio`` mention (case-insensitive, word-bounded) anywhere in a text body
#: or photo caption — the sole trigger (together with a reply to an assistant message)
#: for dispatching a chat message to the assistant pipeline (D18).
_MENTION_PATTERN = re.compile(r"(^|\s)@folio\b", re.IGNORECASE)


def mentions_assistant_token(text: str) -> bool:
    """True when ``text`` contains an ``@folio`` mention."""
    return bool(_MENTION_PATTERN.search(text))


def strip_assistant_mention(text: str) -> str:
    """Remove every ``@folio`` token (and the extra whitespace it leaves behind).

    Used by the assistant pipeline to build the text it actually routes/answers on — a
    message that is only ``"@folio"`` strips down to an empty string, which the router
    treats as a bare greeting.
    """
    return " ".join(_MENTION_PATTERN.sub(" ", text).split())


# What a message renders as. "text"/"photo" are used by user- and assistant-authored
# messages alike; "card"/"choice"/"job_status" are assistant-only rich replies.
CONTENT_TYPES: frozenset[str] = frozenset({"text", "photo", "card", "choice", "job_status"})

SENDER_TYPES: frozenset[str] = frozenset({"user", "assistant", "system"})

MAX_BODY_LEN = 4000


@dataclass(frozen=True)
class ChannelRef:
    """Parsed channel key ``<kind>:<uuid>``."""

    kind: str
    id: UUID

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @classmethod
    def parse(cls, key: str) -> ChannelRef:
        """Parse ``company:<uuid>`` / ``project:<uuid>``; raises ValueError otherwise."""
        kind, sep, raw_id = key.partition(":")
        if not sep or kind not in CHANNEL_KINDS:
            raise ValueError(f"Invalid channel key '{key}'.")
        try:
            return cls(kind=kind, id=UUID(raw_id))
        except ValueError as exc:
            raise ValueError(f"Invalid channel key '{key}'.") from exc


@dataclass(frozen=True)
class ChatAttachment:
    """Image attached to a message; bytes live in object storage under ``storage_key``."""

    storage_key: str
    filename: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class ChatMessage:
    """Immutable chat message. A message carries text, an attachment, or both.

    ``sender_id`` is ``None`` for an assistant-authored message (``sender_type ==
    "assistant"``); every field after ``created_at`` defaults so every existing caller
    that builds a plain user message keeps working unchanged.
    """

    id: UUID
    channel: ChannelRef
    sender_id: UUID | None
    body: str | None
    attachment: ChatAttachment | None
    created_at: datetime
    sender_type: str = "user"
    content_type: str = "text"
    payload: dict[str, Any] | None = None
    reply_to_id: UUID | None = None
    # True when the body/caption carries an ``@folio`` mention, or the message replies to
    # an assistant-authored one — the only messages ever forwarded to the assistant
    # pipeline (see ``app.application.chat.usecases.SendMessageUseCase``) and the only
    # ones a channel's ``list_recent_addressed`` conversation history ever includes.
    mentions_assistant: bool = False
    ai_trace_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        channel: ChannelRef,
        sender_id: UUID,
        body: str | None,
        attachment: ChatAttachment | None,
        payload: dict[str, Any] | None = None,
        reply_to_id: UUID | None = None,
        mentions_assistant: bool = False,
    ) -> ChatMessage:
        """Validate and build a new user-authored message.

        ``content_type`` is inferred: ``"photo"`` when the attachment is an image,
        ``"text"`` otherwise (a voice note still counts as "text" — there is no
        dedicated bubble type for it).

        Raises:
            ValueError: body longer than MAX_BODY_LEN, or neither body nor attachment.
        """
        text = body.strip() if body else None
        if text is not None and len(text) > MAX_BODY_LEN:
            raise ValueError(f"Message body must not exceed {MAX_BODY_LEN} characters.")
        if not text and attachment is None:
            raise ValueError("A message needs a body or an attachment.")
        content_type = "photo" if attachment is not None and attachment.content_type.startswith("image/") else "text"
        return cls(
            id=uuid4(),
            channel=channel,
            sender_id=sender_id,
            body=text or None,
            attachment=attachment,
            created_at=datetime.now(timezone.utc),
            sender_type="user",
            content_type=content_type,
            payload=payload,
            reply_to_id=reply_to_id,
            mentions_assistant=mentions_assistant,
        )

    @classmethod
    def assistant(
        cls,
        *,
        channel: ChannelRef,
        content_type: str,
        payload: dict[str, Any] | None,
        body: str | None,
        reply_to_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> ChatMessage:
        """Build an assistant-authored reply (``sender_id`` is None).

        ``body`` is the plain-text fallback shown by clients that do not render the
        richer ``content_type`` (older app builds, the web widget): it is required even
        for a card/choice/job_status message.

        Raises:
            ValueError: unknown content_type, or no body fallback / body too long.
        """
        if content_type not in CONTENT_TYPES:
            raise ValueError(f"Invalid content_type '{content_type}'.")
        text = body.strip() if body else None
        if not text:
            raise ValueError("An assistant message needs a text fallback body.")
        if len(text) > MAX_BODY_LEN:
            raise ValueError(f"Message body must not exceed {MAX_BODY_LEN} characters.")
        return cls(
            id=uuid4(),
            channel=channel,
            sender_id=None,
            body=text,
            attachment=None,
            created_at=datetime.now(timezone.utc),
            sender_type="assistant",
            content_type=content_type,
            payload=payload,
            reply_to_id=reply_to_id,
            ai_trace_id=trace_id,
        )
