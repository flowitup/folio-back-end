"""Unit tests for `app.application.assistant.features._photos.read_photo_bytes`.

Covers the channel-ownership guard added for review finding C1 (defense in depth): a
chat photo from a channel the caller does not own must never be read/OCR'd through this
path, even if a forged `message_id` ever reached a feature handler.
"""

from __future__ import annotations

import io
from typing import Optional
from uuid import UUID, uuid4

from app.application.assistant.features._photos import read_photo_bytes
from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage
from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage

_PHOTO_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


class FakeMessageRepo:
    def __init__(self) -> None:
        self.messages: dict[UUID, ChatMessage] = {}

    def add(self, message: ChatMessage) -> None:
        self.messages[message.id] = message

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]:
        return self.messages.get(message_id)

    def update_payload(self, message_id: UUID, payload: dict) -> None:  # pragma: no cover - unused here
        raise NotImplementedError

    def list_recent_addressed(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:  # pragma: no cover
        return []


def _photo_message(channel: ChannelRef, storage: InMemoryDocumentStorage) -> ChatMessage:
    key = f"chat/{uuid4()}"
    storage.put(key, io.BytesIO(_PHOTO_BYTES), content_type="image/jpeg")
    return ChatMessage.create(
        channel=channel,
        sender_id=channel.id,
        body=None,
        attachment=ChatAttachment(
            storage_key=key, filename="p.jpg", content_type="image/jpeg", size_bytes=len(_PHOTO_BYTES)
        ),
    )


def test_reads_a_photo_in_the_caller_s_own_assistant_channel() -> None:
    messages = FakeMessageRepo()
    storage = InMemoryDocumentStorage()
    user_id = uuid4()
    message = _photo_message(ChannelRef(kind="assistant", id=user_id), storage)
    messages.add(message)

    result = read_photo_bytes(messages, storage, message.id, user_id)

    assert result is not None
    data, filename, mime = result
    assert data == _PHOTO_BYTES
    assert filename == "p.jpg"
    assert mime == "image/jpeg"


def test_refuses_a_photo_from_someone_elses_assistant_channel() -> None:
    """Review finding C1(d): a forged `message_id` pointing at another user's chat photo
    must never be read, even via a legitimate call site."""
    messages = FakeMessageRepo()
    storage = InMemoryDocumentStorage()
    victim_id = uuid4()
    attacker_id = uuid4()
    message = _photo_message(ChannelRef(kind="assistant", id=victim_id), storage)
    messages.add(message)

    assert read_photo_bytes(messages, storage, message.id, attacker_id) is None


def test_refuses_a_photo_from_a_project_or_company_channel() -> None:
    """Same primitive, a different channel kind — a project/company chat photo the
    caller may not even be a member of must never be OCR'd into their own assistant
    conversation."""
    messages = FakeMessageRepo()
    storage = InMemoryDocumentStorage()
    user_id = uuid4()
    message = _photo_message(ChannelRef(kind="project", id=uuid4()), storage)
    messages.add(message)

    assert read_photo_bytes(messages, storage, message.id, user_id) is None


def test_returns_none_when_the_message_does_not_exist() -> None:
    messages = FakeMessageRepo()
    storage = InMemoryDocumentStorage()
    assert read_photo_bytes(messages, storage, uuid4(), uuid4()) is None
