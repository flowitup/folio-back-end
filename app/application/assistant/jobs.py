"""RQ job entry points for the ``assistant`` queue.

Each job builds its own Flask app (an RQ worker is a separate process with no request
context) and resolves the wired ``AssistantService`` from the DI container — the same
pattern the operational scripts under ``scripts/`` use.

Every entry point re-checks ``FEATURE_ASSISTANT``/the API keys via
``assistant_flags_enabled`` before touching the AI pipeline (review finding NEW-H3):
``RqAssistantDispatcher`` already refuses to *enqueue* while the flag is off, but a job
enqueued before the flag was flipped off would otherwise still run to completion once an
RQ worker picks it up — this is the second, consumption-side half of the kill switch.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def _assistant_enabled(app: Any) -> bool:
    """Reads the same three flags (``FEATURE_ASSISTANT`` + both API keys) that gate the
    synchronous chat surface — see ``app.api.v1.chat.routes.assistant_enabled``. Kept
    local (no Flask-request-scoped ``current_app``) since a job has an app context but
    never a request context."""
    from config import assistant_flags_enabled

    cfg = app.config
    return assistant_flags_enabled(
        cfg.get("FEATURE_ASSISTANT"), cfg.get("DEEPSEEK_API_KEY"), cfg.get("TYPESAFE_API_KEY")
    )


def handle_message(user_id: str, message_id: str) -> None:
    """A user sent a message in their assistant conversation (queued by chat's
    ``SendMessageUseCase`` through ``RqAssistantDispatcher``)."""
    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        if not _assistant_enabled(app):
            logger.info("assistant.jobs.handle_message skipped (FEATURE_ASSISTANT off) message_id=%s", message_id)
            return
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
        if not _assistant_enabled(app):
            logger.info(
                "assistant.jobs.handle_action skipped (FEATURE_ASSISTANT off) message_id=%s action=%s",
                message_id,
                action,
            )
            return
        container = get_container()
        if container.assistant_service is None:
            logger.error("assistant_service not wired; dropping action %s on message %s", action, message_id)
            return
        container.assistant_service.handle_action(
            user_id=UUID(user_id), message_id=UUID(message_id), action=action, payload=payload
        )


def process_fetched_invoice(job_id: str) -> None:
    """Feature B's ``on_result``: the ``ai-browser`` container reported a
    ``fetch_invoice`` job's outcome (enqueued by ``app.infrastructure.browser_worker``,
    a separate process/container with no Flask app of its own — see that package)."""
    import uuid as _uuid

    from app import create_app
    from wiring import get_container

    app = create_app()
    with app.app_context():
        if not _assistant_enabled(app):
            logger.info("assistant.jobs.process_fetched_invoice skipped (FEATURE_ASSISTANT off) job_id=%s", job_id)
            return
        container = get_container()
        if container.assistant_invoice_fetch_feature is None or container.assistant_messenger is None:
            logger.error("assistant invoice-fetch feature not wired; dropping job result %s", job_id)
            return
        trace_id = _uuid.uuid4().hex[:16]
        container.assistant_invoice_fetch_feature.on_result(
            UUID(job_id), messenger=container.assistant_messenger, trace_id=trace_id
        )
