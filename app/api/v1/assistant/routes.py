"""Assistant actions API.

The only synchronous endpoint of the assistant bounded context: a user tapping a choice
option. Every reply arrives asynchronously through chat's existing message list / poll,
dispatched by ``SendMessageUseCase.assistant_dispatcher`` (a sent message) or by
``SubmitAssistantActionUseCase`` (a submitted action) — see
``app.application.assistant.jobs``.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api._helpers.validation_error import safe_validation_fields
from app.api.openapi import openapi_doc
from app.api.v1.assistant import assistant_bp
from app.api.v1.assistant.schemas import ActionAcceptedResponse, SubmitActionBody
from app.api.v1.chat.routes import assistant_enabled
from app.application.assistant.exceptions import (
    AssistantAlreadyAnsweredError,
    AssistantMessageNotFoundError,
    AssistantNotAddressedError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


@assistant_bp.post("/assistant/actions")
@openapi_doc(
    summary="Answer an assistant choice message",
    request=SubmitActionBody,
    responses={202: ActionAcceptedResponse},
    tags=["assistant"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def submit_action() -> Any:
    if not assistant_enabled():
        return _err(404, "FeatureDisabled", "The assistant is not enabled on this server.")
    try:
        parsed = SubmitActionBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.submit_assistant_action_usecase is None:
        raise RuntimeError("submit_assistant_action_usecase not wired in container")
    try:
        container.submit_assistant_action_usecase.execute(
            actor_id=actor_id, action=parsed.action, payload=parsed.payload, reply_to_id=parsed.reply_to_id
        )
    except AssistantMessageNotFoundError:
        return _err(404, "NotFound", "Message not found in your assistant conversation")
    except AssistantNotAddressedError:
        return _err(403, "NotAddressed", "This choice was not addressed to you")
    except AssistantAlreadyAnsweredError:
        return _err(409, "AlreadyAnswered", "This choice has already been answered")
    except Exception:
        logger.exception("submit_action unexpected error")
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"accepted": True}), 202
