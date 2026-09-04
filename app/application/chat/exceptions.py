"""Chat bounded-context exceptions (mapped to HTTP statuses in the routes)."""

from __future__ import annotations


class ChatError(Exception):
    """Base class for chat errors."""


class ChatChannelNotFoundError(ChatError):
    """The channel key is malformed or names a company / project that does not exist."""


class NotChannelMemberError(ChatError):
    """The actor is not attached to the company / not a member of the project."""


class ChatMessageNotFoundError(ChatError):
    """No message with that id."""


class EmptyMessageError(ChatError):
    """Neither a body nor an attachment was supplied."""


class AttachmentTooLargeError(ChatError):
    """Attachment exceeds the size cap."""


class UnsupportedAttachmentTypeError(ChatError):
    """Attachment content type is not an allowed image type."""
