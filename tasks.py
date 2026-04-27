"""
Background Tasks

This module defines background tasks that can be enqueued for async processing.
Tasks are designed to be run by RQ workers.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import logging

from app.infrastructure.email.exceptions import EmailDeliveryError

logger = logging.getLogger(__name__)


@dataclass
class EmailPayload:
    """Email task payload structure."""

    to: str
    subject: str
    body: str
    html_body: Optional[str] = None
    from_address: Optional[str] = None


def send_email(payload: EmailPayload) -> None:
    """
    Send an email via the configured EmailPort adapter.

    Resolves the email adapter from the DI container and delegates sending.
    On EmailDeliveryError the exception is re-raised so the RQ retry policy
    (exponential backoff) can handle transient failures.

    Args:
        payload: EmailPayload dataclass with recipient, subject, and bodies.

    Raises:
        EmailDeliveryError: propagated from the adapter on delivery failure.
    """
    from wiring import get_container  # local import avoids circular deps at module load

    adapter = get_container().email_port
    if adapter is None:
        logger.warning("email_port not configured in container — email not sent to %s", payload.to)
        return

    try:
        adapter.send(payload)
    except EmailDeliveryError:
        logger.error("Email delivery failed for <%s>", payload.to)
        raise


def process_notification(payload: Dict[str, Any]) -> bool:
    """
    Process a notification task.

    Stub implementation for notification processing.

    Args:
        payload: Notification payload

    Returns:
        True if processed successfully
    """
    logger.info("[STUB] process_notification called with payload: %s", payload)
    return True
