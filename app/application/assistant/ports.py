"""Ports (Protocols) of the assistant application layer."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.domain.entities.chat_message import ChatMessage


class AssistantDispatcherPort(Protocol):
    """Post-commit hand-off to the assistant pipeline, run out of band.

    The production adapter enqueues an RQ job; tests use an in-memory recorder. A
    failure here must never surface to the caller — sending a chat message (or
    accepting an action) has already committed by the time this runs.
    """

    def message_received(self, *, user_id: UUID, message_id: UUID) -> None:
        """A user sent a message in their assistant conversation."""
        ...

    def action_received(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> None:
        """A user tapped a choice option (``POST /api/v1/assistant/actions``)."""
        ...


class MessagePosterPort(Protocol):
    """What ``AssistantMessenger`` needs to persist an assistant-authored chat message."""

    def add(self, message: ChatMessage) -> None: ...

    def find_by_id(self, message_id: UUID) -> ChatMessage | None: ...

    def update_payload(self, message_id: UUID, payload: dict[str, Any]) -> None: ...
