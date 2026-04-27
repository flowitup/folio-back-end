"""Admin API routes — superadmin-gated endpoints: bulk-add memberships + user search."""

import logging
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api.v1.admin import admin_bp
from app.api.v1.admin.schemas import (
    BulkAddRequest,
    BulkAddResponse,
    BulkAddResultItem,
    UserSearchItem,
    UserSearchResponse,
)
from app.api.v1.auth.schemas import ErrorResponse
from app.application.admin.exceptions import (
    EmptyProjectListError,
    PermissionDeniedError,
    RoleNotAllowedError,
    RoleNotFoundError,
    TargetUserNotFoundError,
    TooManyProjectsError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)

_MAX_SEARCH_LEN = 100  # reject queries longer than this (defense against LIKE abuse)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jwt_user_key() -> str:
    """Rate-limit key scoped to authenticated JWT identity (falls back to IP)."""
    try:
        uid = get_jwt_identity()
        return f"user:{uid}" if uid else request.remote_addr
    except Exception:
        return request.remote_addr


def _err(code: int, error: str, message: str):
    return jsonify(ErrorResponse(error=error, message=message, status_code=code).model_dump()), code


def _validation_err(e: ValidationError):
    fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
    return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")


def _require_superadmin():
    """Return 403 tuple if caller lacks *:* permission, else None."""
    claims = get_jwt()
    perms = set(claims.get("permissions", []))
    if "*:*" not in perms:
        return _err(403, "Forbidden", "Superadmin required.")
    return None


# ---------------------------------------------------------------------------
# POST /admin/users/<uuid:user_id>/memberships
# ---------------------------------------------------------------------------


@admin_bp.route("/users/<uuid:user_id>/memberships", methods=["POST"])
@jwt_required()
@limiter.limit("5 per hour", key_func=_jwt_user_key)
@limiter.limit("10 per hour")
def bulk_add_memberships(user_id: UUID):
    """Bulk-add an existing user to multiple projects with the given role.

    Gated to superadmin (*:*) permission. Rate-limited: 5/h per caller user,
    10/h per IP (Flask-Limiter applies the strictest matching limit).
    """
    guard = _require_superadmin()
    if guard is not None:
        return guard

    try:
        data = BulkAddRequest(**request.get_json(silent=True) or {})
    except ValidationError as e:
        return _validation_err(e)

    requester_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        dto = container.bulk_add_existing_user_usecase.execute(
            requester_id=requester_id,
            target_user_id=user_id,
            project_ids=data.project_ids,
            role_id=data.role_id,
        )
    except PermissionDeniedError as e:
        return _err(403, "Forbidden", str(e))
    except TargetUserNotFoundError as e:
        return _err(404, "NotFound", str(e))
    except RoleNotFoundError as e:
        return _err(404, "NotFound", str(e))
    except RoleNotAllowedError as e:
        return _err(403, "Forbidden", str(e))
    except (EmptyProjectListError, TooManyProjectsError) as e:
        return _err(400, "BadRequest", str(e))
    except Exception:
        logger.exception("bulk_add_memberships unexpected error for user_id=%s", user_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    response = BulkAddResponse(
        results=[
            BulkAddResultItem(
                project_id=r.project_id,
                project_name=r.project_name,
                status=r.status.value,
            )
            for r in dto.results
        ]
    )
    return jsonify(response.model_dump()), 200


# ---------------------------------------------------------------------------
# GET /admin/users?search=<q>&limit=<1..20>
# ---------------------------------------------------------------------------


@admin_bp.route("/users", methods=["GET"])
@jwt_required()
@limiter.limit("30 per minute", key_func=_jwt_user_key)
def search_users():
    """Search users by email or display name (superadmin only).

    Query params:
      search  — substring to match against email / display_name (required, max 100 chars)
      limit   — max results; clamped to [1, 20] (default 20)

    Decision: queries longer than 100 chars are rejected with 400 (not silently
    truncated) to surface misconfigured callers and prevent LIKE-clause abuse.
    """
    guard = _require_superadmin()
    if guard is not None:
        return guard

    q = request.args.get("search", "").strip()
    if not q:
        return jsonify(UserSearchResponse(items=[], count=0).model_dump()), 200

    if len(q) > _MAX_SEARCH_LEN:
        return _err(400, "BadRequest", f"search query must be {_MAX_SEARCH_LEN} characters or fewer.")

    try:
        raw_limit = int(request.args.get("limit", 20))
    except (ValueError, TypeError):
        raw_limit = 20
    limit = max(1, min(raw_limit, 20))

    container = get_container()
    users = container.user_repository.search_by_email_or_name(q, limit)

    response = UserSearchResponse(
        items=[
            UserSearchItem(
                id=u.id,
                email=u.email,
                display_name=u.display_name,
            )
            for u in users
        ],
        count=len(users),
    )
    return jsonify(response.model_dump()), 200
