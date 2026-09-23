"""Assistant actions API.

The only synchronous endpoint of the assistant bounded context: a user tapping a choice
option. Every reply arrives asynchronously through chat's existing message list / poll,
dispatched by ``SendMessageUseCase.assistant_dispatcher`` (a sent message) or by
``SubmitAssistantActionUseCase`` (a submitted action) — see
``app.application.assistant.jobs``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api._helpers.validation_error import safe_validation_fields
from app.api.openapi import openapi_doc
from app.api.v1.ops_context import is_platform_ops
from app.api.v1.assistant import assistant_bp
from app.api.v1.assistant.schemas import ActionAcceptedResponse, AssistantAuditListResponse, SubmitActionBody
from app.api.v1.chat.routes import assistant_enabled
from app.application.assistant.exceptions import (
    AssistantAlreadyAnsweredError,
    AssistantMessageNotFoundError,
    AssistantNotAddressedError,
)
from app.application.assistant.service import AssistantDispatchFailedError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class _InvalidQueryParam(ValueError):
    """Raised by `_require_datetime` for a `from`/`to` value that isn't a valid
    ISO 8601 date/datetime — distinguishes "absent" (fine, no filter) from "present but
    unparsable" (a 422, M5), which `_parse_datetime` alone cannot tell apart."""


def _require_datetime(param: str, value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = _parse_datetime(value)
    if parsed is None:
        raise _InvalidQueryParam(f"{param} must be an ISO 8601 date or datetime")
    return parsed


#: `GET /assistant/audit`'s own clamp on `limit` (M5) — 1..500, default 200. Mirrors
#: `submit_action`'s rate limit below so a company admin cannot hammer this DB-heavy,
#: otherwise-cheap-to-call endpoint.
_AUDIT_LIMIT_MIN = 1
_AUDIT_LIMIT_MAX = 200
_AUDIT_LIMIT_DEFAULT = 200


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
    except AssistantDispatchFailedError:
        return _err(503, "AssistantUnavailable", "Could not hand this off for processing, please retry.")
    except Exception:
        logger.exception("submit_action unexpected error")
        return _err(500, "InternalError", "An unexpected error occurred.")
    return jsonify({"accepted": True}), 202


@assistant_bp.get("/assistant/audit")
@openapi_doc(
    summary="List the assistant's supervision log for a company (admin only)",
    responses={200: AssistantAuditListResponse},
    tags=["assistant"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def get_audit() -> Any:
    """D17 layer 4: one row per handled mention, readable by a company admin of
    ``company_id`` or platform ops — the admin channel's own "who asked what" answer and
    the web supervision page share this endpoint."""
    if not assistant_enabled():
        return _err(404, "FeatureDisabled", "The assistant is not enabled on this server.")
    company_id_raw = request.args.get("company_id")
    if not company_id_raw:
        return _err(422, "ValidationError", "company_id is required")
    try:
        company_id = UUID(company_id_raw)
    except ValueError:
        return _err(422, "ValidationError", "company_id must be a UUID")

    caller_id = UUID(get_jwt_identity())
    container = get_container()
    if container.assistant_audit_repo is None or container.authz_reader is None or container.chat_repo is None:
        raise RuntimeError("assistant audit dependencies not wired in container")

    if not is_platform_ops() and container.authz_reader.company_role_for(caller_id, company_id) != "admin":
        return _err(403, "Forbidden", "Only a company admin or platform ops may read the audit log.")

    user_id_raw = request.args.get("user_id")
    try:
        user_id = UUID(user_id_raw) if user_id_raw else None
    except ValueError:
        return _err(422, "ValidationError", "user_id must be a UUID")
    limit_raw = request.args.get("limit")
    try:
        limit = max(_AUDIT_LIMIT_MIN, min(int(limit_raw), _AUDIT_LIMIT_MAX)) if limit_raw else _AUDIT_LIMIT_DEFAULT
    except ValueError:
        return _err(422, "ValidationError", "limit must be an integer")

    try:
        from_ = _require_datetime("from", request.args.get("from"))
        to = _require_datetime("to", request.args.get("to"))
    except _InvalidQueryParam as exc:
        return _err(422, "ValidationError", str(exc))

    rows = container.assistant_audit_repo.list_for_company(
        company_id,
        from_=from_,
        to=to,
        user_id=user_id,
        limit=limit,
    )
    names = container.chat_repo.display_names([row.user_id for row in rows if row.user_id is not None])
    items = [
        {
            "id": str(row.id),
            "created_at": row.created_at.isoformat(),
            "channel_key": row.channel_key,
            "user_id": str(row.user_id) if row.user_id is not None else None,
            "user_name": names.get(row.user_id, "?") if row.user_id is not None else "?",
            "intent": row.intent,
            "feature": row.feature,
            "outcome": row.outcome,
            "refused_reason": row.refused_reason,
            "cost_usd": float(row.cost_usd),
            "trace_id": row.trace_id,
        }
        for row in rows
    ]
    return jsonify({"items": items})
