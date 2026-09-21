"""Shared helper: read a chat photo message's bytes for the feature C/A pipelines."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.assistant.ports import MessagePosterPort
from app.application.chat.ports import ChatAttachmentStoragePort
from app.domain.entities.chat_message import ChannelRef


def read_photo_bytes(
    messages: MessagePosterPort, storage: ChatAttachmentStoragePort, message_id: UUID, user_id: UUID
) -> Optional[tuple[bytes, str, str]]:
    """Return (bytes, filename, mime_type) for a photo message, or None.

    None covers every reason the photo cannot be read: the message does not exist, it
    is not in ``user_id``'s own assistant channel (defense in depth — a chat photo from
    a project/company channel the caller is not even a member of must never be OCR'd or
    attached to that caller's own invoice/product), it has no image attachment, or the
    storage read fails — callers treat all of these the same way (ask the user to
    retake/resend the photo).
    """
    message = messages.find_by_id(message_id)
    if (
        message is None
        or message.channel != ChannelRef(kind="assistant", id=user_id)
        or message.attachment is None
        or not message.attachment.content_type.startswith("image/")
    ):
        return None
    try:
        stream, _length = storage.get_stream(message.attachment.storage_key)
        data = stream.read()
    except Exception:
        return None
    if not data:
        return None
    return data, message.attachment.filename, message.attachment.content_type
