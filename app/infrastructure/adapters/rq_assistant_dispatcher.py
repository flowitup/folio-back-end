"""Production AssistantDispatcherPort: enqueues assistant pipeline jobs on RQ.

A push failure must never break sending a chat message or accepting an action, so every
failure here (Redis unreachable, serialization error, ...) is caught and logged rather
than raised — mirrors ``ChatPushNotifier``'s own "never break the caller" contract.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from redis import Redis
from rq import Queue

logger = logging.getLogger(__name__)

QUEUE_NAME = "assistant"


class RqAssistantDispatcher:
    """Implements AssistantDispatcherPort against a real Redis-backed RQ queue.

    Jobs are enqueued by dotted path (``"app.application.assistant.jobs.handle_message"``)
    rather than by importing the function, so the web process never needs to import the
    (heavier, AI-dependency-laden) assistant pipeline module just to hand off a message.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    def _queue(self) -> Queue:
        return Queue(QUEUE_NAME, connection=Redis.from_url(self._redis_url))

    def message_received(self, *, user_id: UUID, message_id: UUID) -> None:
        try:
            self._queue().enqueue("app.application.assistant.jobs.handle_message", str(user_id), str(message_id))
        except Exception:
            logger.exception("assistant dispatch failed (message_received) message_id=%s", message_id)

    def action_received(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> None:
        try:
            self._queue().enqueue(
                "app.application.assistant.jobs.handle_action", str(user_id), str(message_id), action, payload
            )
        except Exception:
            logger.exception("assistant dispatch failed (action_received) message_id=%s action=%s", message_id, action)
