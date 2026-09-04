"""Outbound SMS port. Adapters: logging (dev/test) and Twilio (production)."""

from __future__ import annotations

from typing import Protocol


class SmsSendError(Exception):
    """The provider refused or failed to send the message."""


class SmsSenderPort(Protocol):
    def send(self, to: str, text: str) -> None:
        """Send ``text`` to the E.164 number ``to``; raise ``SmsSendError`` on failure."""
        ...
