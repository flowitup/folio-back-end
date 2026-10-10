"""ChatMessage domain entity — one line of team chat in a company, project or admin channel.

Channels are virtual: a message belongs to ``(channel_kind, channel_id)`` where
``channel_kind`` is ``"company"`` (every company member), ``"project"`` (one channel per
project) or ``"admin"`` (one per-company channel for that company's admins and platform
ops, keyed by the company's id). Membership is derived from company access rows and
project memberships at read time — there is no channel table.

Legacy rows written by a retired feature may still exist in the database (a channel kind
``"assistant"``, ``sender_type == "assistant"`` with a NULL ``sender_id``, rich content types
such as ``"card"``). They are never written any more but must stay readable: listing messages
tolerates them, and ``ChannelRef.parse`` rejects the retired channel kind like any unknown one.
Every message created today is authored by a user and carries the actor's id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

CHANNEL_KINDS: frozenset[str] = frozenset({"company", "project", "admin"})

# What a message renders as. New messages are "text" or "photo"; the other values only
# exist on legacy rows and are kept so those rows still load.
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

    ``sender_id`` is ``None`` only for legacy rows written by a retired feature
    (``sender_type == "assistant"``).
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
        )
