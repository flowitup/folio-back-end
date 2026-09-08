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
    ProjectCompanyUnresolvedError,
    TargetNotCompanyMemberError,
)
from app.domain.companies.exceptions import UserCompanyAccessNotFoundError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


class AssignMemberRequest(BaseModel):
    """PUT /projects/<id>/assignments/<user_id> body.

    `role` is COMPANY-WIDE, not per project: `manager` raises the target's
    company role to manager (company admins only), which grants manager
    permissions on every project they are assigned to in that company.
    `member` is the default and never demotes anyone.
    """

    role: str = Field(default="member", pattern=r"^(member|manager)$")


def _err(error: str, message: str, status: int):
    return jsonify({"error": error, "message": message}), status


def _notify_membership(event: str, *, user_id: UUID, actor_id: UUID, entity_id: UUID, role=None) -> None:
    """Fire-and-forget; the notifier swallows its own failures."""
    notifier = get_container().membership_push_notifier
    if notifier is not None:
        notifier.notify(event, user_id=user_id, actor_id=actor_id, entity_id=entity_id, role=role)


@projects_bp.route("/<project_id>/assignments/<user_id>", methods=["PUT"])
@openapi_doc(
    summary="Assign a company member to a project; a company admin may pass role=manager to promote the target",
    request=AssignMemberRequest,
    tags=["projects"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_project_access(write=True, permission="project:manage_users")
def assign_project_member(project_id: str, user_id: str):
    """Assign a company member to this project (idempotent).

    Optional body `{"role": "member" | "manager"}`: `manager` asks to raise the
    target's COMPANY role to manager (company admins only — the role lives on
    the company, the assignment only says who works on this project); `member`
    never demotes anyone. The response echoes the target's company role.

    A refused promotion rolls the assignment back: the request either performs
    both writes or neither.
    """
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

    from app import db

    try:
        company_role = container.assign_project_member_usecase.execute(
            AssignProjectMemberInput(
                caller_id=caller_id,
                project_id=UUID(project_id),
                target_user_id=target_uuid,
                role=body.role,
            )
        )
    except ProjectCompanyUnresolvedError:
        db.session.rollback()
        return _err("NotFound", f"Project {project_id} not found", 404)
    except TargetNotCompanyMemberError:
        db.session.rollback()
        return _err("NotFound", f"User {user_id} is not a member of this project's company", 404)
    except UserCompanyAccessNotFoundError:
        # The access row was detached between the read and the promotion.
        db.session.rollback()
        return _err("NotFound", f"User {user_id} is not a member of this project's company", 404)
    except AssignmentForbiddenError as exc:
        db.session.rollback()
        return _err("Forbidden", str(exc), 403)
    except ValueError as exc:
        db.session.rollback()
        return _err("ValidationError", str(exc), 400)

    db.session.commit()
    _notify_membership(
        "project_member_added",
        user_id=target_uuid,
        actor_id=caller_id,
        entity_id=UUID(project_id),
        role=company_role,
    )
    return jsonify({"project_id": project_id, "user_id": user_id, "role": company_role}), 200


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
    _notify_membership("project_member_removed", user_id=target_uuid, actor_id=caller_id, entity_id=UUID(project_id))
    return "", 204
