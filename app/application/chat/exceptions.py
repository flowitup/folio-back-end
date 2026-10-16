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


class ReplyTargetNotInChannelError(ChatError):
    """``reply_to_id`` names a message that does not exist, or exists in a different channel."""


class CannotBlockSelfError(ChatError):
    """A user tried to block themselves."""


class BlockTargetNotFoundError(ChatError):
    """The user to block does not exist or shares no company or project with the actor."""


class InvalidReactionError(ChatError):
    """The emoji is not one of the reactions a message accepts."""


class CannotReportOwnMessageError(ChatError):
    """A user tried to report a message they sent."""


class ChatReportNotFoundError(ChatError):
    """No report with that id (or it belongs to a company the actor does not administer)."""


class NotCompanyAdminError(ChatError):
    """Only the company's admins review chat reports."""


class AttachmentContentMismatchError(UnsupportedAttachmentTypeError):
    """The attachment's bytes are not the image / voice note its content type claims."""
