"""Assistant pipeline entry point and the synchronous action-submission use case.

``AssistantService`` is intentionally small in phase 01: it posts one template text
reply so the vertical slice (send a message -> RQ job -> assistant reply -> app polls it)
is testable end to end. Phase 02 replaces ``handle_message``'s body with the real
DeepSeek + TypeSafe routing/extraction pipeline; ``handle_action`` is the seam a tapped
choice option dispatches into.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.application.assistant.exceptions import AssistantAlreadyAnsweredError, AssistantMessageNotFoundError
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.ports import AssistantDispatcherPort, MessagePosterPort
from app.application.invitations.ports import TransactionalSessionPort

logger = logging.getLogger(__name__)

# fr is the deployment's current locale default (see config.Config.PUSH_LOCALE); vi/en
# are the two other languages the mobile app ships (D9 — the web widget stays text-only).
_GREETING = {
    "vi": "Xin chào! Gửi cho tôi ảnh hoá đơn hoặc vật liệu, hoặc hỏi tôi một dụng cụ đang ở đâu.",
    "fr": "Bonjour ! Envoie-moi la photo d'un ticket ou d'un matériau, ou demande-moi où se trouve un outil.",
    "en": "Hi! Send me a photo of a receipt or a material, or ask me where a tool is.",
}
_PHOTO_RECEIVED = {
    "vi": "Đã nhận ảnh.",
    "fr": "Photo reçue.",
    "en": "Photo received.",
}
_DEFAULT_LOCALE = "fr"


class AssistantService:
    """Called by the RQ jobs (``app.application.assistant.jobs``)."""

    def __init__(self, message_repo: MessagePosterPort, messenger: AssistantMessenger) -> None:
        self._messages = message_repo
        self._messenger = messenger

    def handle_message(self, *, user_id: UUID, message_id: UUID) -> None:
        message = self._messages.find_by_id(message_id)
        if message is None:
            logger.warning("assistant handle_message: message %s not found", message_id)
            return
        lang = (message.payload or {}).get("lang") if message.content_type == "text" else None
        locale = lang if lang in _GREETING else _DEFAULT_LOCALE
        reply = _PHOTO_RECEIVED[locale] if message.content_type == "photo" else _GREETING[locale]
        self._messenger.post_text(user_id, reply, reply_to_id=message.id)

    def handle_action(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> None:
        # Phase 01 has no action-driven behaviour yet; phase 02 dispatches on `action`.
        logger.info("assistant handle_action: user=%s message=%s action=%s", user_id, message_id, action)


class SubmitAssistantActionUseCase:
    """``POST /api/v1/assistant/actions``: the caller answers a choice message.

    Marks the choice ``payload.answered`` and hands off to the dispatcher; the pipeline
    itself (``AssistantService.handle_action``) runs out of band.
    """

    def __init__(
        self,
        message_repo: MessagePosterPort,
        db_session: TransactionalSessionPort,
        dispatcher: AssistantDispatcherPort,
    ) -> None:
        self._messages = message_repo
        self._db = db_session
        self._dispatcher = dispatcher

    def execute(self, *, actor_id: UUID, action: str, payload: dict[str, Any], reply_to_id: UUID) -> None:
        message = self._messages.find_by_id(reply_to_id)
        if (
            message is None
            or message.channel.kind != "assistant"
            or message.channel.id != actor_id
            or message.content_type != "choice"
        ):
            raise AssistantMessageNotFoundError(
                f"Message {reply_to_id} is not a choice in {actor_id}'s assistant conversation."
            )
        current_payload = dict(message.payload or {})
        if current_payload.get("answered"):
            raise AssistantAlreadyAnsweredError(f"Message {reply_to_id} was already answered.")
        current_payload["answered"] = action
        self._messages.update_payload(reply_to_id, current_payload)
        self._db.commit()
        # After the commit: a dispatch failure must never be able to roll back the answer.
        self._dispatcher.action_received(user_id=actor_id, message_id=reply_to_id, action=action, payload=payload)
