"""Invitation API routes — 5 endpoints per phase-05 spec."""

from uuid import UUID

from flask import jsonify, make_response, request
from flask_jwt_extended import get_jwt_identity, jwt_required, set_access_cookies, set_refresh_cookies
from pydantic import ValidationError

from app.api.v1.invitations import invitations_bp
from app.api.v1.invitations.schemas import (
    AcceptInviteRequest,
    AcceptedUserResponse,
    CreateInviteRequest,
    CreateInviteResponse,
    InvitationListItem,
    InvitationListResponse,
    VerifyInviteResponse,
)
from app.api.v1.auth.schemas import ErrorResponse
from app.application.invitations.exceptions import (
    PermissionDeniedError,
    RateLimitedError,
    RoleNotFoundError,
    ProjectNotFoundError,
)
from app.domain.exceptions.invitation_exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
    InvalidInvitationTokenError,
    RoleNotAllowedError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _jwt_user_key() -> str:
    """Rate-limit key based on JWT identity (user ID) for authenticated endpoints."""
    from flask_jwt_extended import get_jwt_identity as _get_jwt_identity
    try:
        uid = _get_jwt_identity()
        return f"user:{uid}" if uid else request.remote_addr
    except Exception:
        return request.remote_addr


def _err(code: int, error: str, message: str):
    return jsonify(ErrorResponse(error=error, message=message, status_code=code).model_dump()), code


def _validation_err(e: ValidationError):
    fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
    return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")


# ---------------------------------------------------------------------------
# POST /api/v1/invitations
# ---------------------------------------------------------------------------

@invitations_bp.route("", methods=["POST"])
@jwt_required()
@limiter.limit("10 per hour", key_func=_jwt_user_key)
def create_invitation():
    """Create an invitation (or directly add existing user) to a project."""
    try:
        data = CreateInviteRequest(**request.get_json(silent=True) or {})
    except ValidationError as e:
        return _validation_err(e)

    container = get_container()
    user_id = UUID(get_jwt_identity())

    # Owner-check fallback: load project to compare owner_id
    project = None
    if container.create_invitation_usecase is None:
        return _err(503, "ServiceUnavailable", "Invitation service not configured.")

    # Pre-check: allow project owner even without explicit permission
    project = None
    try:
        project = container.project_repository.find_by_id(data.project_id)
    except Exception:
        pass

    from flask_jwt_extended import get_jwt
    claims = get_jwt()
    permissions = set(claims.get("permissions", []))
    is_superadmin = "*:*" in permissions
    has_perm = "project:invite" in permissions
    is_owner = project is not None and project.owner_id == user_id

    if not (is_superadmin or has_perm or is_owner):
        return _err(403, "Forbidden", "You do not have permission to invite users to this project.")

    try:
        result = container.create_invitation_usecase.execute(
            inviter_id=user_id,
            project_id=data.project_id,
            email=str(data.email),
            role_id=data.role_id,
        )
    except PermissionDeniedError as e:
        return _err(403, "Forbidden", str(e))
    except ProjectNotFoundError as e:
        return _err(404, "NotFound", str(e))
    except RoleNotFoundError as e:
        return _err(404, "NotFound", str(e))
    except RoleNotAllowedError as e:
        return _err(403, "Forbidden", str(e))
    except RateLimitedError as e:
        return _err(429, "RateLimited", str(e))
    except Exception:
        return _err(500, "InternalError", "An unexpected error occurred.")

    response_data = CreateInviteResponse(
        kind=result.kind,
        invitation_id=result.invitation_id,
        expires_at=result.expires_at,
        user_id=result.user_id,
    )
    return jsonify(response_data.model_dump()), 201


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<uuid:project_id>/invitations
# ---------------------------------------------------------------------------

@invitations_bp.route("/projects/<uuid:project_id>/invitations", methods=["GET"])
@jwt_required()
@limiter.limit("60 per minute")
def list_project_invitations(project_id: UUID):
    """List invitations for a project. Requires project membership."""
    container = get_container()
    if container.list_invitations_usecase is None:
        return _err(503, "ServiceUnavailable", "Invitation service not configured.")

    user_id = UUID(get_jwt_identity())
    status_filter = request.args.get("status", "pending")

    try:
        items = container.list_invitations_usecase.execute(
            requester_id=user_id,
            project_id=project_id,
            status_filter=status_filter,
        )
    except PermissionDeniedError as e:
        return _err(403, "Forbidden", str(e))
    except Exception:
        return _err(500, "InternalError", "An unexpected error occurred.")

    response = InvitationListResponse(
        items=[
            InvitationListItem(
                id=item.id,
                email=item.email,
                role_name=item.role_name,
                status=item.status.value,
                expires_at=item.expires_at,
                created_at=item.created_at,
                invited_by_name=item.invited_by_name,
            )
            for item in items
        ]
    )
    return jsonify(response.model_dump()), 200


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/<uuid:invitation_id>/revoke
# ---------------------------------------------------------------------------

@invitations_bp.route("/<uuid:invitation_id>/revoke", methods=["POST"])
@jwt_required()
@limiter.limit("30 per minute", key_func=_jwt_user_key)
def revoke_invitation(invitation_id: UUID):
    """Revoke a pending invitation."""
    container = get_container()
    if container.revoke_invitation_usecase is None:
        return _err(503, "ServiceUnavailable", "Invitation service not configured.")

    user_id = UUID(get_jwt_identity())

    try:
        container.revoke_invitation_usecase.execute(
            inviter_id=user_id,
            invitation_id=invitation_id,
        )
    except InvitationNotFoundError as e:
        return _err(404, "NotFound", str(e))
    except PermissionDeniedError as e:
        return _err(403, "Forbidden", str(e))
    except Exception:
        return _err(500, "InternalError", "An unexpected error occurred.")

    return "", 204


# ---------------------------------------------------------------------------
# GET /api/v1/invitations/verify/<token>  — public
# ---------------------------------------------------------------------------

@invitations_bp.route("/verify/<token>", methods=["GET"])
@limiter.limit("60 per minute")
def verify_invitation(token: str):
    """Verify an invitation token and return safe metadata. Public endpoint."""
    container = get_container()
    if container.verify_invitation_usecase is None:
        return _err(503, "ServiceUnavailable", "Invitation service not configured.")

    try:
        dto = container.verify_invitation_usecase.execute(raw_token=token)
    except InvalidInvitationTokenError:
        return _err(404, "NotFound", "Invitation not found.")
    except (InvitationExpiredError, InvitationRevokedError, InvitationAlreadyAcceptedError) as e:
        return _err(410, "Gone", str(e))
    except Exception as e:
        # Surface DB-level errors (e.g. missing table in test env) as 500
        import logging
        logging.getLogger(__name__).exception("verify_invitation error: %s", e)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(
        VerifyInviteResponse(
            email=dto.email,
            project_name=dto.project_name,
            role_name=dto.role_name,
            inviter_name=dto.inviter_name,
            expires_at=dto.expires_at,
        ).model_dump()
    ), 200


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/accept  — public
# ---------------------------------------------------------------------------

@invitations_bp.route("/accept", methods=["POST"])
@limiter.limit("5 per minute")
def accept_invitation():
    """Accept an invitation: create account + membership, return JWT cookies."""
    try:
        data = AcceptInviteRequest(**request.get_json(silent=True) or {})
    except ValidationError as e:
        return _validation_err(e)

    container = get_container()
    if container.accept_invitation_usecase is None:
        return _err(503, "ServiceUnavailable", "Invitation service not configured.")

    try:
        result = container.accept_invitation_usecase.execute(
            raw_token=data.token,
            name=data.name,
            password=data.password,
        )
    except InvalidInvitationTokenError:
        return _err(404, "NotFound", "Invitation not found.")
    except (InvitationExpiredError, InvitationRevokedError, InvitationAlreadyAcceptedError) as e:
        return _err(410, "Gone", str(e))
    except ValueError as e:
        return _err(422, "ValidationError", str(e))
    except Exception:
        return _err(500, "InternalError", "An unexpected error occurred.")

    user_data = AcceptedUserResponse(
        id=result.user.id,
        email=result.user.email,
        display_name=result.user.display_name,
    )
    response = make_response(jsonify({"user": user_data.model_dump()}), 200)
    set_access_cookies(response, result.access_token)
    set_refresh_cookies(response, result.refresh_token)
    return response
