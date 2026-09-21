"""Assistant bounded-context exceptions (mapped to HTTP statuses in the routes)."""

from __future__ import annotations


class AssistantError(Exception):
    """Base class for assistant errors."""


class AssistantMessageNotFoundError(AssistantError):
    """``reply_to_id`` is unknown, not a choice, or the submitted action/payload does not
    byte-for-byte match one of the choice's own stored options."""


class AssistantAlreadyAnsweredError(AssistantError):
    """The choice this action replies to has already been answered."""


class AssistantNotAddressedError(AssistantError):
    """The caller is not a member of the choice's channel, or the choice was not
    addressed to them (``payload["addressed_to"]``) — only the asker may answer."""


class ProviderNotConfiguredError(AssistantError):
    """An AI provider adapter was called but its API key is not configured.

    Raised by every ``NullX`` port implementation (`app.infrastructure.ai`). The
    pipeline (`AssistantService`) always catches this and answers the "not
    configured" template instead of letting the request crash — a deployment that
    turned `FEATURE_ASSISTANT` on before adding every key still degrades gracefully.
    """


class LlmOutputError(AssistantError):
    """DeepSeek's JSON reply failed pydantic validation twice (temperature 0 then 0.2)."""


class DecisionError(AssistantError):
    """Jev (TypeSafe) raised while making a routing/extraction decision."""
