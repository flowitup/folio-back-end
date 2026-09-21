"""Production AssistantDispatcherPort: enqueues assistant pipeline jobs on RQ.

A push failure must never break sending a chat message or accepting an action, so every
failure here (Redis unreachable, serialization error, ...) is caught and logged rather
than raised — mirrors ``ChatPushNotifier``'s own "never break the caller" contract.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional
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

    ``assistant_enabled`` is the same ``Config.assistant_enabled``-backed callable
    injected into ``SqlAlchemyChatRepository`` — a second, independent kill switch: even
    if something upstream ever enqueued a job while the flag is off (or a stale job sits
    in the queue from before it was turned off), this never actually calls into the AI
    pipeline while `FEATURE_ASSISTANT`/its keys are not configured.
    """

    def __init__(self, redis_url: str, assistant_enabled: Optional[Callable[[], bool]] = None) -> None:
        self._redis_url = redis_url
        self._assistant_enabled = assistant_enabled or (lambda: True)

    def _queue(self) -> Queue:
        return Queue(QUEUE_NAME, connection=Redis.from_url(self._redis_url))

    def message_received(self, *, user_id: UUID, message_id: UUID) -> None:
        if not self._assistant_enabled():
            logger.info("assistant dispatch skipped (FEATURE_ASSISTANT off) message_id=%s", message_id)
            return
        try:
            self._queue().enqueue("app.application.assistant.jobs.handle_message", str(user_id), str(message_id))
        except Exception:
            logger.exception("assistant dispatch failed (message_received) message_id=%s", message_id)

    def action_received(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> None:
        if not self._assistant_enabled():
            logger.info(
                "assistant dispatch skipped (FEATURE_ASSISTANT off) message_id=%s action=%s", message_id, action
            )
            return
        try:
            self._queue().enqueue(
                "app.application.assistant.jobs.handle_action", str(user_id), str(message_id), action, payload
            )
        except Exception:
            logger.exception("assistant dispatch failed (action_received) message_id=%s action=%s", message_id, action)
