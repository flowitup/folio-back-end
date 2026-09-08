"""Project assignment routes (Phase 2 onboarding): manager/member assignment.

  PUT    /projects/<project_id>/assignments/<user_id>  → assign (admin: any role; manager: member only)
  DELETE /projects/<project_id>/assignments/<user_id>  → unassign

Distinct from invitations (outsider path, no membership precondition):
assignment targets an EXISTING company member and never creates an account.
"""

from __future__ import annotations

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import BaseModel, Field, ValidationError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.projects import projects_bp
from app.api.v1.projects.decorators import require_project_access
from app.application.projects.assignments import (
    AssignmentForbiddenError,
    AssignProjectMemberInput,
    InvalidAssignmentRoleError,
    ProjectCompanyUnresolvedError,
    TargetNotCompanyMemberError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


class AssignMemberRequest(BaseModel):
    """PUT /projects/<id>/assignments/<user_id> body."""

    role: str = Field(default="member", pattern=r"^(member|manager)$")


def _err(error: str, message: str, status: int):
    return jsonify({"error": error, "message": message}), status


@projects_bp.route("/<project_id>/assignments/<user_id>", methods=["PUT"])
@openapi_doc(
    summary="Assign a company member to a project (admin: any role; manager: member only)",
    request=AssignMemberRequest,
    tags=["projects"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_project_access(write=True)
def assign_project_member(project_id: str, user_id: str):
    """Create or change a manager/member assignment on this project."""
    try:
        target_uuid = UUID(user_id)
    except ValueError:
        return _err("NotFound", f"User {user_id} not found", 404)

    try:
        body = AssignMemberRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        container.assign_project_member_usecase.execute(
            AssignProjectMemberInput(
                caller_id=caller_id,
                project_id=UUID(project_id),
                target_user_id=target_uuid,
                role=body.role,
            )
        )
    except ProjectCompanyUnresolvedError:
        return _err("NotFound", f"Project {project_id} not found", 404)
    except AssignmentForbiddenError as exc:
        return _err("Forbidden", str(exc), 403)
    except InvalidAssignmentRoleError as exc:
        return _err("ValidationError", str(exc), 400)
    except TargetNotCompanyMemberError:
        return _err("NotFound", f"User {user_id} is not a member of this project's company", 404)

    from app import db

    db.session.commit()
    return jsonify({"project_id": project_id, "user_id": user_id, "role": body.role}), 200


@projects_bp.route("/<project_id>/assignments/<user_id>", methods=["DELETE"])
@openapi_doc(summary="Remove a project assignment", tags=["projects"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_project_access(write=True)
def unassign_project_member(project_id: str, user_id: str):
    """Remove a manager/member assignment from this project."""
    try:
        target_uuid = UUID(user_id)
    except ValueError:
        return _err("NotFound", f"User {user_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        container.unassign_project_member_usecase.execute(caller_id, UUID(project_id), target_uuid)
    except ProjectCompanyUnresolvedError:
        return _err("NotFound", f"Project {project_id} not found", 404)
    except AssignmentForbiddenError as exc:
        return _err("Forbidden", str(exc), 403)
    except TargetNotCompanyMemberError:
        return _err("NotFound", f"User {user_id} is not a member of this project's company", 404)

    from app import db

    db.session.commit()
    return "", 204
