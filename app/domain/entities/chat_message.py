"""ChatMessage domain entity — one line of team chat in a company or project channel.

Channels are virtual: a message belongs to ``(channel_kind, channel_id)`` where
``channel_kind`` is ``"company"`` (the company-wide "Chung" channel) or ``"project"``
(one channel per project). Membership is derived from company access rows and
project memberships at read time — there is no channel table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

CHANNEL_KINDS: frozenset[str] = frozenset({"company", "project"})

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
    """Immutable chat message. A message carries text, an attachment, or both."""

    id: UUID
    channel: ChannelRef
    sender_id: UUID
    body: str | None
    attachment: ChatAttachment | None
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        channel: ChannelRef,
        sender_id: UUID,
        body: str | None,
        attachment: ChatAttachment | None,
    ) -> ChatMessage:
        """Validate and build a new message.

        Raises:
            ValueError: body longer than MAX_BODY_LEN, or neither body nor attachment.
        """
        text = body.strip() if body else None
        if text is not None and len(text) > MAX_BODY_LEN:
            raise ValueError(f"Message body must not exceed {MAX_BODY_LEN} characters.")
        if not text and attachment is None:
            raise ValueError("A message needs a body or an attachment.")
        return cls(
            id=uuid4(),
            channel=channel,
            sender_id=sender_id,
            body=text or None,
            attachment=attachment,
            created_at=datetime.now(timezone.utc),
        )
