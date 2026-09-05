"""Notifications API routes — user-scoped due-reminder endpoints."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from flask import Response, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.notifications import notifications_bp
from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


# ---------------------------------------------------------------------------
# GET /api/v1/notifications
# ---------------------------------------------------------------------------


@notifications_bp.get("/notifications")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("120 per minute", key_func=jwt_user_key)
def list_notifications() -> Any:
    """Lazy-compute the current user's notifications. Hard cap: 100 per kind.

    ``items`` keeps its note-only shape (existing clients index ``item.note``);
    worker-submitted attendance awaiting this user's validation ships in the
    separate ``attendance_pending`` list. ``count`` covers both.
    """
    user_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_due_notifications_usecase is None:
        raise RuntimeError("list_due_notifications_usecase not wired in container")
    if container.list_pending_attendance_usecase is None:
        raise RuntimeError("list_pending_attendance_usecase not wired in container")

    try:
        dtos = container.list_due_notifications_usecase.execute(user_id=user_id)
        pending = container.list_pending_attendance_usecase.execute(user_id=user_id)
    except Exception:
        logger.exception("list_notifications unexpected error user_id=%s", user_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    items = [
        {
            "kind": "note_due",
            "note": {
                "id": str(dto.note.id),
                "project_id": str(dto.note.project_id),
                "created_by": str(dto.note.created_by),
                "title": dto.note.title,
                "description": dto.note.description,
                "category": dto.note.category,
                "created_at": dto.note.created_at.isoformat(),
                "updated_at": dto.note.updated_at.isoformat(),
            },
            "dismissed": dto.dismissed,
        }
        for dto in dtos
    ]

    attendance_pending = [
        {
            "kind": "attendance_pending",
            "entry_id": p.entry_id,
            "project_id": p.project_id,
            "project_name": p.project_name,
            "worker_id": p.worker_id,
            "worker_name": p.worker_name,
            "date": p.date,
            "shift_type": p.shift_type,
            "supplement_hours": p.supplement_hours,
            "note": p.note,
            "submitted_at": p.submitted_at,
        }
        for p in pending
    ]

    response = jsonify(
        {"items": items, "attendance_pending": attendance_pending, "count": len(items) + len(attendance_pending)}
    )
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response, 200


# ---------------------------------------------------------------------------
# POST /api/v1/notifications/<uuid:note_id>/dismiss
# ---------------------------------------------------------------------------


@notifications_bp.post("/notifications/<uuid:note_id>/dismiss")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def dismiss_notification(note_id: UUID) -> Any:
    """Dismiss a note notification for the current user. Idempotent."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.dismiss_notification_usecase is None:
        raise RuntimeError("dismiss_notification_usecase not wired in container")

    try:
        container.dismiss_notification_usecase.execute(
            actor_id=actor_id,
            note_id=note_id,
        )
    except NoteNotFoundError:
        return _err(404, "NotFound", "Note not found")
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("dismiss_notification unexpected error note_id=%s", note_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return "", 204
