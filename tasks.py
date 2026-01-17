"""
Background Tasks

This module defines background tasks that can be enqueued for async processing.
Tasks are designed to be run by RQ workers.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class EmailPayload:
    """Email task payload structure."""

    to: str
    subject: str
    body: str
    html_body: Optional[str] = None
    from_address: Optional[str] = None


def send_email(payload: Dict[str, Any]) -> bool:
    """
    Send an email task.

    This is a stub implementation. In production, this would use
    the configured email service from the DI container.

    Args:
        payload: Email payload containing:
            - to: Recipient email address
            - subject: Email subject
            - body: Plain text body
            - html_body: Optional HTML body
            - from_address: Optional sender address

    Returns:
        True if email was sent successfully, False otherwise
    """
    logger.info(f"[STUB] send_email called with payload: {payload}")

    # Validate payload
    required_fields = ["to", "subject", "body"]
    for field in required_fields:
        if field not in payload:
            logger.error(f"Missing required field: {field}")
            return False

    # TODO: Implement actual email sending using the configured email service
    # from wiring import get_container
    # container = get_container()
    # if container.email_service:
    #     return container.email_service.send_email(
    #         to=payload["to"],
    #         subject=payload["subject"],
    #         body=payload["body"],
    #         html_body=payload.get("html_body"),
    #     )

    logger.info(f"[STUB] Would send email to: {payload['to']}")
    logger.info(f"[STUB] Subject: {payload['subject']}")

    return True


def process_notification(payload: Dict[str, Any]) -> bool:
    """
    Process a notification task.

    Stub implementation for notification processing.

    Args:
        payload: Notification payload

    Returns:
        True if processed successfully
    """
    logger.info(f"[STUB] process_notification called with payload: {payload}")

    # TODO: Implement notification processing

    return True
