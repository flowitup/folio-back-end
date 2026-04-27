"""Notes API routes — 4 project-scoped CRUD endpoints."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api.v1.notes import notes_bp
from app.api.v1.notes.schemas import NoteCreateBody, NoteResponse, NoteUpdateBody
from app.application.notes.dtos import NoteDto
from app.application.notes.exceptions import (
    InvalidLeadTimeError,
    NoteNotFoundError,
    NotProjectMemberError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _jwt_user_key() -> str:
    """Rate-limit key scoped to authenticated JWT identity (falls back to IP)."""
    try:
        uid = get_jwt_identity()
        return f"user:{uid}" if uid else (request.remote_addr or "unknown")
    except Exception:
        return request.remote_addr or "unknown"


def _to_response(dto: NoteDto) -> NoteResponse:
    """Map a NoteDto to a NoteResponse Pydantic model."""
    return NoteResponse(
        id=dto.id,
        project_id=dto.project_id,
        created_by=dto.created_by,
        title=dto.title,
        description=dto.description,
        due_date=dto.due_date,
        lead_time_minutes=dto.lead_time_minutes,
        status=dto.status,
        fire_at=dto.fire_at,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
    )


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


# ---------------------------------------------------------------------------
# POST /api/v1/projects/<uuid:project_id>/notes
# ---------------------------------------------------------------------------


@notes_bp.post("/projects/<uuid:project_id>/notes")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=_jwt_user_key)
def create_note(project_id: UUID) -> Any:
    """Create a note for a project. Actor must be a project member."""
    try:
        body = NoteCreateBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = [e.get("loc", ["unknown"])[-1] for e in exc.errors()]
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    assert container.create_note_usecase is not None, "create_note_usecase not wired"

    try:
        note_dto = container.create_note_usecase.execute(
            actor_id=actor_id,
            project_id=project_id,
            title=body.title,
            description=body.description,
            due_date=body.due_date,
            lead_time_minutes=body.lead_time_minutes,
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except (InvalidLeadTimeError, ValueError) as exc:
        return _err(400, "BadRequest", str(exc))
    except Exception:
        logger.exception("create_note unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(_to_response(note_dto).model_dump(mode="json")), 201


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<uuid:project_id>/notes
# ---------------------------------------------------------------------------


@notes_bp.get("/projects/<uuid:project_id>/notes")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=_jwt_user_key)
def list_notes(project_id: UUID) -> Any:
    """List all notes for a project. Actor must be a project member."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    assert container.list_project_notes_usecase is not None, "list_project_notes_usecase not wired"

    try:
        dtos = container.list_project_notes_usecase.execute(
            actor_id=actor_id,
            project_id=project_id,
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("list_notes unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    items = [_to_response(d).model_dump(mode="json") for d in dtos]
    return jsonify({"items": items, "count": len(items)}), 200


# ---------------------------------------------------------------------------
# PATCH /api/v1/projects/<uuid:project_id>/notes/<uuid:note_id>
# ---------------------------------------------------------------------------


@notes_bp.patch("/projects/<uuid:project_id>/notes/<uuid:note_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=_jwt_user_key)
def update_note(project_id: UUID, note_id: UUID) -> Any:
    """Update a note's fields. Dismissal-cascade fires if due_date/lead_time changes."""
    try:
        body = NoteUpdateBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = [e.get("loc", ["unknown"])[-1] for e in exc.errors()]
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    assert container.update_note_usecase is not None, "update_note_usecase not wired"
    assert container.mark_note_done_usecase is not None, "mark_note_done_usecase not wired"
    assert container.mark_note_open_usecase is not None, "mark_note_open_usecase not wired"

    try:
        # Apply field updates (title, description, due_date, lead_time_minutes).
        note_dto = container.update_note_usecase.execute(
            actor_id=actor_id,
            note_id=note_id,
            title=body.title,
            description=body.description,
            due_date=body.due_date,
            lead_time_minutes=body.lead_time_minutes,
        )

        # Apply status transition separately if requested.
        if body.status == "done":
            note_dto = container.mark_note_done_usecase.execute(
                actor_id=actor_id,
                note_id=note_id,
            )
        elif body.status == "open":
            note_dto = container.mark_note_open_usecase.execute(
                actor_id=actor_id,
                note_id=note_id,
            )

    except NoteNotFoundError:
        return _err(404, "NotFound", "Note not found")
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except (InvalidLeadTimeError, ValueError) as exc:
        return _err(400, "BadRequest", str(exc))
    except Exception:
        logger.exception("update_note unexpected error note_id=%s", note_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(_to_response(note_dto).model_dump(mode="json")), 200


# ---------------------------------------------------------------------------
# DELETE /api/v1/projects/<uuid:project_id>/notes/<uuid:note_id>
# ---------------------------------------------------------------------------


@notes_bp.delete("/projects/<uuid:project_id>/notes/<uuid:note_id>")
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=_jwt_user_key)
def delete_note(project_id: UUID, note_id: UUID) -> Any:
    """Delete a note. Actor must be a project member."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    assert container.delete_note_usecase is not None, "delete_note_usecase not wired"

    try:
        container.delete_note_usecase.execute(
            actor_id=actor_id,
            note_id=note_id,
        )
    except NoteNotFoundError:
        return _err(404, "NotFound", "Note not found")
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("delete_note unexpected error note_id=%s", note_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return "", 204
