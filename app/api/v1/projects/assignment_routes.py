"""Project assignment routes (Phase 2 onboarding): manager/member assignment.

  PUT    /projects/<project_id>/assignments/<user_id>  → assign
  DELETE /projects/<project_id>/assignments/<user_id>  → unassign

An assignment carries no role — it says "this person works on this project"
and nothing more. Distinct from invitations (the outsider path, with no
membership precondition): assignment names an EXISTING company member and
never creates an account.
"""

from __future__ import annotations

from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.projects import projects_bp
from app.api.v1.projects.decorators import require_project_access
from app.application.projects.assignments import (
    AssignmentForbiddenError,
    AssignProjectMemberInput,
    ProjectCompanyUnresolvedError,
    TargetNotCompanyMemberError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _err(error: str, message: str, status: int):
    return jsonify({"error": error, "message": message}), status


@projects_bp.route("/<project_id>/assignments/<user_id>", methods=["PUT"])
@openapi_doc(
    summary="Assign a company member to a project (admin: anyone; manager: company members only)",
    tags=["projects"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_project_access(write=True, permission="project:manage_users")
def assign_project_member(project_id: str, user_id: str):
    """Assign a company member to this project (idempotent, no request body)."""
    try:
        target_uuid = UUID(user_id)
    except ValueError:
        return _err("NotFound", f"User {user_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        container.assign_project_member_usecase.execute(
            AssignProjectMemberInput(
                caller_id=caller_id,
                project_id=UUID(project_id),
                target_user_id=target_uuid,
            )
        )
    except ProjectCompanyUnresolvedError:
        return _err("NotFound", f"Project {project_id} not found", 404)
    except AssignmentForbiddenError as exc:
        return _err("Forbidden", str(exc), 403)
    except TargetNotCompanyMemberError:
        return _err("NotFound", f"User {user_id} is not a member of this project's company", 404)

    from app import db

    db.session.commit()
    return jsonify({"project_id": project_id, "user_id": user_id}), 200


@projects_bp.route("/<project_id>/assignments/<user_id>", methods=["DELETE"])
@openapi_doc(summary="Remove a project assignment", tags=["projects"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_project_access(write=True, permission="project:manage_users")
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
