"""EmailPort — the canonical email-sending contract.

Replaces the legacy ``send_email(to, subject, body, html_body)`` Protocol that
previously lived in ``wiring.py`` and was shadowed by the new ``EmailPayload``
based adapters added during the invitation feature work.

Implementations:
- ``app.infrastructure.email.resend_adapter.ResendEmailAdapter`` (production)
- ``app.infrastructure.email.inmemory_adapter.InMemoryEmailAdapter`` (tests)
"""

from typing import Protocol


class EmailPort(Protocol):
    """Send a single transactional email payload.

    Implementations MUST raise ``EmailDeliveryError`` (or a subclass) on
    failure so the RQ worker's retry policy can engage.
    """

    def send(self, payload: "EmailPayload") -> None:  # noqa: F821 — forward ref
        ...
