"""API-keys API routes — create, list, and revoke personal automation credentials.

A key inherits its owner's permissions in full (no scope, no company
pinning, no expiry — locked product decisions), so every route here is
gated on the CALLER'S identity only, via `UUID(get_jwt_identity())`. There
is no `require_permission`/`require_project_access` layer to add: a user
manages only their own keys, never anyone else's.

Self-escalation guard: a request already authenticated BY an API key (see
`app/api/_helpers/api_key_request_auth.py`) must never be able to reach
these routes — otherwise a leaked key could enumerate its siblings, mint
itself replacements forever, or revoke another key to cover its tracks. The
blueprint-level `before_request` below answers 403 for that case before any
handler runs: managing keys always requires an interactive (JWT-from-login)
session, never a key acting on its own behalf.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.api_key_request_auth import reject_api_key_callers
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api._helpers.validation_error import safe_validation_fields
from app.api.openapi import openapi_doc
from app.api.v1.api_keys import api_keys_bp
from app.api.v1.api_keys.schemas import CreateApiKeyBody
from app.application.api_keys.dtos import ApiKeyDto
from app.application.api_keys.exceptions import (
    ApiKeyLimitReachedError,
    ApiKeyNotFoundError,
    InvalidApiKeyNameError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


def _serialize_key(dto: ApiKeyDto) -> dict[str, Any]:
    """Serialize an ApiKeyDto to a JSON-safe dict. Never includes the token or its hash."""
    return {
        "id": str(dto.id),
        "name": dto.name,
        "prefix": dto.prefix,
        "created_at": dto.created_at.isoformat(),
        "last_used_at": dto.last_used_at.isoformat() if dto.last_used_at else None,
    }


# A request authenticated BY an API key may never manage API keys — see
# reject_api_key_callers for why. The same module carries the companion rule
# applied to /auth/* and /admin/* (a key may read them, never mutate them).
api_keys_bp.before_request(reject_api_key_callers)


# ---------------------------------------------------------------------------
# GET /api/v1/api-keys
# ---------------------------------------------------------------------------


@api_keys_bp.get("/api-keys")
@openapi_doc(summary="List the caller's API keys", tags=["api-keys"])
@jwt_required()  # type: ignore[untyped-decorator]
def list_api_keys() -> Any:
    """List the caller's own API keys, newest first. Never returns the token or its hash."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_api_keys_usecase is None:
        raise RuntimeError("list_api_keys_usecase not wired in container")

    dtos = container.list_api_keys_usecase.execute(user_id=actor_id)
    return jsonify({"api_keys": [_serialize_key(d) for d in dtos]}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/api-keys
# ---------------------------------------------------------------------------


@api_keys_bp.post("/api-keys")
@openapi_doc(
    summary="Create a new API key for the caller",
    request=CreateApiKeyBody,
    tags=["api-keys"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("10 per minute", key_func=jwt_user_key)
def create_api_key() -> Any:
    """Create an API key. The plaintext token is returned exactly once, in this response."""
    try:
        body = CreateApiKeyBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.create_api_key_usecase is None:
        raise RuntimeError("create_api_key_usecase not wired in container")

    try:
        created = container.create_api_key_usecase.execute(user_id=actor_id, name=body.name)
    except InvalidApiKeyNameError as exc:
        return _err(400, "BadRequest", str(exc))
    except ApiKeyLimitReachedError as exc:
        return _err(409, "Conflict", str(exc))
    except Exception:
        logger.exception("create_api_key unexpected error user_id=%s", actor_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    payload = _serialize_key(created)
    payload["token"] = created.token
    return jsonify(payload), 201


# ---------------------------------------------------------------------------
# DELETE /api/v1/api-keys/<uuid:key_id>
# ---------------------------------------------------------------------------


@api_keys_bp.delete("/api-keys/<uuid:key_id>")
@openapi_doc(summary="Revoke one of the caller's API keys", tags=["api-keys"])
@jwt_required()  # type: ignore[untyped-decorator]
def revoke_api_key(key_id: UUID) -> Any:
    """Revoke (permanently delete) one of the caller's own API keys."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.revoke_api_key_usecase is None:
        raise RuntimeError("revoke_api_key_usecase not wired in container")

    try:
        container.revoke_api_key_usecase.execute(user_id=actor_id, key_id=key_id)
    except ApiKeyNotFoundError:
        return _err(404, "NotFound", "API key not found")
    except Exception:
        logger.exception("revoke_api_key unexpected error key_id=%s", key_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return "", 204
