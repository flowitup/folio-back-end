"""Tags API routes — CRUD + tag-summary for project-scoped phase tags."""

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
from app.api.v1.tags import tags_bp
from app.api.v1.tags.schemas import TagCreateBody, TagUpdateBody
from app.application.tags.dtos import CreateTagDto, UpdateTagDto
from app.application.tags.exceptions import (
    DuplicateProjectTagNameError,
    InvalidProjectTagError,
    NotProjectMemberError,
    ProjectTagNotFoundError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


def _serialize_tag(dto: Any) -> dict:
    return {
        "id": str(dto.id),
        "project_id": str(dto.project_id),
        "name": dto.name,
        "color": dto.color,
        "created_at": dto.created_at.isoformat(),
        "updated_at": dto.updated_at.isoformat() if dto.updated_at else None,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<project_id>/tags
# ---------------------------------------------------------------------------


@tags_bp.get("/projects/<uuid:project_id>/tags")
@openapi_doc(summary="List all phase tags for a project", tags=["tags"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def list_tags(project_id: UUID) -> Any:
    """List all phase tags for a project. Actor must be a project member."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        dtos = container.list_project_tags_usecase.execute(
            actor_id=actor_id,
            project_id=project_id,
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("list_tags unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    items = [_serialize_tag(d) for d in dtos]
    return jsonify({"items": items, "count": len(items)}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/projects/<project_id>/tags
# ---------------------------------------------------------------------------


@tags_bp.post("/projects/<uuid:project_id>/tags")
@openapi_doc(
    summary="Create a new phase tag for a project",
    request=TagCreateBody,
    tags=["tags"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=jwt_user_key)
def create_tag(project_id: UUID) -> Any:
    """Create a new phase tag scoped to a project."""
    try:
        body = TagCreateBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")

    actor_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        dto = container.create_project_tag_usecase.execute(
            CreateTagDto(
                project_id=project_id,
                actor_id=actor_id,
                name=body.name,
                color=body.color,
            )
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except DuplicateProjectTagNameError:
        return _err(409, "Conflict", f"Tag '{body.name}' already exists in this project")
    except (InvalidProjectTagError, ValueError) as exc:
        return _err(400, "BadRequest", str(exc))
    except Exception:
        logger.exception("create_tag unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(_serialize_tag(dto)), 201


# ---------------------------------------------------------------------------
# PUT /api/v1/projects/<project_id>/tags/<tag_id>
# ---------------------------------------------------------------------------


@tags_bp.put("/projects/<uuid:project_id>/tags/<uuid:tag_id>")
@openapi_doc(
    summary="Update a phase tag's name and/or color",
    request=TagUpdateBody,
    tags=["tags"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=jwt_user_key)
def update_tag(project_id: UUID, tag_id: UUID) -> Any:
    """Update name and/or color of an existing phase tag."""
    try:
        body = TagUpdateBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = safe_validation_fields(exc)
        return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")

    actor_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        dto = container.update_project_tag_usecase.execute(
            UpdateTagDto(
                tag_id=tag_id,
                project_id=project_id,
                actor_id=actor_id,
                name=body.name,
                color=body.color,
            )
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except ProjectTagNotFoundError:
        return _err(404, "NotFound", f"Tag {tag_id} not found")
    except DuplicateProjectTagNameError:
        return _err(409, "Conflict", f"Tag '{body.name}' already exists in this project")
    except (InvalidProjectTagError, ValueError) as exc:
        return _err(400, "BadRequest", str(exc))
    except Exception:
        logger.exception("update_tag unexpected error tag_id=%s", tag_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(_serialize_tag(dto)), 200


# ---------------------------------------------------------------------------
# DELETE /api/v1/projects/<project_id>/tags/<tag_id>
# ---------------------------------------------------------------------------


@tags_bp.delete("/projects/<uuid:project_id>/tags/<uuid:tag_id>")
@openapi_doc(summary="Delete a phase tag (downstream entries/invoices get tag_id=NULL)", tags=["tags"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=jwt_user_key)
def delete_tag(project_id: UUID, tag_id: UUID) -> Any:
    """Delete a phase tag. Linked entries/invoices keep their data; tag_id becomes NULL."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        container.delete_project_tag_usecase.execute(
            actor_id=actor_id,
            project_id=project_id,
            tag_id=tag_id,
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except ProjectTagNotFoundError:
        return _err(404, "NotFound", f"Tag {tag_id} not found")
    except Exception:
        logger.exception("delete_tag unexpected error tag_id=%s", tag_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return "", 204


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<project_id>/tag-summary
# ---------------------------------------------------------------------------


@tags_bp.get("/projects/<uuid:project_id>/tag-summary")
@openapi_doc(summary="Per-tag cost rollup (labor + expenses) for a project", tags=["tags"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def get_tag_summary(project_id: UUID) -> Any:
    """Return per-tag labor cost + expense total rollup.

    Includes one row per tag plus an '(untagged)' bucket for entries/invoices
    with no tag assignment. Tags with zero activity still appear (cost=0).
    """
    actor_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        rows = container.tag_summary_usecase.execute(
            actor_id=actor_id,
            project_id=project_id,
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("get_tag_summary unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return (
        jsonify(
            {
                "rows": [
                    {
                        "tag_id": str(r.tag_id) if r.tag_id is not None else None,
                        "tag_name": r.tag_name,
                        "tag_color": r.tag_color,
                        "labor_cost": float(r.labor_cost),
                        "expense_total": float(r.expense_total),
                        "labor_entry_count": r.labor_entry_count,
                        "invoice_count": r.invoice_count,
                    }
                    for r in rows
                ],
                "count": len(rows),
            }
        ),
        200,
    )
