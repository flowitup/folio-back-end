"""Assistant bounded-context exceptions (mapped to HTTP statuses in the routes)."""

from __future__ import annotations


class AssistantError(Exception):
    """Base class for assistant errors."""


class AssistantMessageNotFoundError(AssistantError):
    """``reply_to_id`` is unknown, not a choice, or not in the caller's assistant channel."""


class AssistantAlreadyAnsweredError(AssistantError):
    """The choice this action replies to has already been answered."""
