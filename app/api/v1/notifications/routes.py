"""Notifications API routes — user-scoped due reminders and push preferences."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.notifications import notifications_bp
from app.api.v1.notifications.schemas import (
    NotificationPreferencesResponse,
    UpdateNotificationPreferencesRequest,
)
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
    separate ``attendance_pending`` list. ``count`` covers both — NOT
    ``company_events`` (Phase 2 onboarding, finding 12: no client has shipped
    UI for this feed yet, so it must not bump the notification badge).
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
        company_events = (
            container.list_new_members_usecase.execute(admin_user_id=user_id)
            if container.list_new_members_usecase is not None
            else []
        )
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
            # attendance_pending = new day to validate; attendance_change = proposed edit on a validated day
            "kind": p.kind,
            "proposed_shift_type": p.proposed_shift_type,
            "proposed_supplement_hours": p.proposed_supplement_hours,
            "proposed_note": p.proposed_note,
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

    company_events_json = [
        {
            "user_id": str(event.user_id),
            "display_name": event.display_name,
            "company_id": str(event.company_id),
            "attached_at": event.attached_at.isoformat(),
        }
        for event in company_events
    ]

    response = jsonify(
        {
            "items": items,
            "attendance_pending": attendance_pending,
            "company_events": company_events_json,
            # NOT company_events — see docstring above (finding 12).
            "count": len(items) + len(attendance_pending),
        }
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


# ---------------------------------------------------------------------------
# GET / PUT /api/v1/notifications/preferences
# ---------------------------------------------------------------------------


def _preferences_repo() -> Any:
    repo = get_container().notification_preference_repository
    if repo is None:
        raise RuntimeError("notification_preference_repository not wired in container")
    return repo


@notifications_bp.get("/notifications/preferences")
@openapi_doc(
    summary="Read the caller's push notification preferences",
    responses={200: NotificationPreferencesResponse},
    tags=["notifications"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def get_notification_preferences() -> Any:
    """All-on is the default, so a user who never changed anything still gets a full body."""
    return jsonify(_preferences_repo().get(UUID(get_jwt_identity())))


@notifications_bp.put("/notifications/preferences")
@openapi_doc(
    summary="Update the caller's push notification preferences (partial)",
    request=UpdateNotificationPreferencesRequest,
    responses={200: NotificationPreferencesResponse},
    tags=["notifications"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=jwt_user_key)
def update_notification_preferences() -> Any:
    """Partial update: omitted fields keep their value, unknown fields are a 422."""
    try:
        body = UpdateNotificationPreferencesRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "ValidationError", exc.errors()[0].get("msg", "invalid body"))

    changes = body.model_dump(exclude_none=True)
    user_id = UUID(get_jwt_identity())
    if not changes:
        return jsonify(_preferences_repo().get(user_id))
    return jsonify(_preferences_repo().update(user_id, changes))
