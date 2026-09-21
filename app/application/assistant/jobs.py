"""RQ job entry points for the ``assistant`` queue.

Each job builds its own Flask app (an RQ worker is a separate process with no request
context) and resolves the wired ``AssistantService`` from the DI container — the same
pattern the operational scripts under ``scripts/`` use.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def handle_message(user_id: str, message_id: str) -> None:
    """A user sent a message in their assistant conversation (queued by chat's
    ``SendMessageUseCase`` through ``RqAssistantDispatcher``)."""
    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        container = get_container()
        if container.assistant_service is None:
            logger.error("assistant_service not wired; dropping message %s", message_id)
            return
        container.assistant_service.handle_message(user_id=UUID(user_id), message_id=UUID(message_id))


def handle_action(user_id: str, message_id: str, action: str, payload: dict[str, Any]) -> None:
    """A user tapped a choice option (queued by ``SubmitAssistantActionUseCase``)."""
    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        container = get_container()
        if container.assistant_service is None:
            logger.error("assistant_service not wired; dropping action %s on message %s", action, message_id)
            return
        container.assistant_service.handle_action(
            user_id=UUID(user_id), message_id=UUID(message_id), action=action, payload=payload
        )
