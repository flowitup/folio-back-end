"""Invitation API routes — 5 endpoints per phase-05 spec."""

from uuid import UUID

from flask import jsonify, make_response, request
from flask_jwt_extended import get_jwt_identity, jwt_required, set_access_cookies, set_refresh_cookies
from pydantic import ValidationError

from app.api.openapi import openapi_doc
from app.api.v1.invitations import invitations_bp
from app.api.v1.invitations.schemas import (
    AcceptInviteRequest,
    AcceptedUserResponse,
    CreateInviteRequest,
    CreateInviteResponse,
    InvitationListItem,
    InvitationListResponse,
    RequestInviteCodeRequest,
    VerifyInviteResponse,
)
from app.api.v1.auth.schemas import ErrorResponse, OtpRequestResponse
from app.application.invitations.exceptions import (
    PermissionDeniedError,
    RateLimitedError,
    ProjectNotFoundError,
)
from app.application.ports.sms_sender import SmsSendError
from app.domain.exceptions.auth_exceptions import (
    OtpInvalidError,
    OtpThrottledError,
    PhoneAlreadyRegisteredError,
)
from app.domain.exceptions.invitation_exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
    InvalidInvitationTokenError,
)
from app.domain.value_objects.phone_number import InvalidPhoneNumberError
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _err(code: int, error: str, message: str):
    return jsonify(ErrorResponse(error=error, message=message, status_code=code).model_dump()), code


def _gone(reason: str, message: str):
    """410 Gone with a `reason` discriminator the frontend uses to pick the error UI.

    reason ∈ {'expired', 'revoked', 'accepted'}.
    """
    body = ErrorResponse(error="Gone", message=message, status_code=410).model_dump()
    body["reason"] = reason
    return jsonify(body), 410


def _conflict(reason: str, message: str):
    """409 Conflict with a `reason` discriminator the frontend uses to pick the error UI.

    reason ∈ {'phone_registered'}.
    """
    body = ErrorResponse(error="Conflict", message=message, status_code=409).model_dump()
    body["reason"] = reason
    return jsonify(body), 409


def _validation_err(e: ValidationError):
    fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
    return _err(422, "ValidationError", f"Invalid input: {', '.join(str(f) for f in fields)}")


# ---------------------------------------------------------------------------
# POST /api/v1/invitations
# ---------------------------------------------------------------------------


@invitations_bp.route("", methods=["POST"])
@openapi_doc(
    summary="Create an invitation (or directly add existing user) to a project",
    request=CreateInviteRequest,
    responses={201: CreateInviteResponse},
    tags=["invitations"],
)
@jwt_required()
@limiter.limit("10 per hour", key_func=jwt_user_key)
def create_invitation():
    """Create an invitation (or directly add existing user) to a project."""
    try:
        data = CreateInviteRequest(**request.get_json(silent=True) or {})
    except ValidationError as e:
        return _validation_err(e)

    container = get_container()
    user_id = UUID(get_jwt_identity())

    if container.create_invitation_usecase is None:
        return _err(503, "ServiceUnavailable", "Invitation service not configured.")

    # Inviting is `project:invite` on THIS project, resolved from the caller's
    # company role + assignment (+ D8 rows). No owner bypass (D6).
    from app.api.v1.projects.decorators import _effective_perms_for

    permissions = set(_effective_perms_for(data.project_id, user_id))
    if not ({"*:*", "project:invite", "project:*"} & permissions):
        return _err(403, "Forbidden", "You do not have permission to invite users to this project.")

    try:
        result = container.create_invitation_usecase.execute(
            inviter_id=user_id,
            project_id=data.project_id,
            email=str(data.email),
        )
    except PermissionDeniedError as e:
        return _err(403, "Forbidden", str(e))
    except ProjectNotFoundError as e:
        return _err(404, "NotFound", str(e))
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
@openapi_doc(summary="List invitations for a project", tags=["invitations"])
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
@openapi_doc(summary="Revoke a pending invitation", tags=["invitations"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
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
@openapi_doc(
    summary="Verify an invitation token and return safe metadata",
    responses={200: VerifyInviteResponse},
    tags=["invitations"],
    auth=False,
)
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
    except InvitationExpiredError as e:
        return _gone("expired", str(e))
    except InvitationRevokedError as e:
        return _gone("revoked", str(e))
    except InvitationAlreadyAcceptedError as e:
        return _gone("accepted", str(e))
    except Exception as e:
        # Surface DB-level errors (e.g. missing table in test env) as 500
        import logging

        logging.getLogger(__name__).exception("verify_invitation error: %s", e)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return (
        jsonify(
            VerifyInviteResponse(
                email=dto.email,
                project_name=dto.project_name,
                role_name=dto.role_name,
                inviter_name=dto.inviter_name,
                expires_at=dto.expires_at,
            ).model_dump()
        ),
        200,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/accept/request-code  — public
# ---------------------------------------------------------------------------


@invitations_bp.route("/accept/request-code", methods=["POST"])
@openapi_doc(
    summary="Text a 6-digit code to the phone number an invitation acceptor is claiming",
    request=RequestInviteCodeRequest,
    responses={202: OtpRequestResponse},
    tags=["invitations"],
    auth=False,
)
@limiter.limit("5 per minute")
def request_invite_code():
    """Text a sign-up code to the phone being claimed. Public: the invitation token is the authorisation."""
    try:
        data = RequestInviteCodeRequest(**request.get_json(silent=True) or {})
    except ValidationError as e:
        return _validation_err(e)

    container = get_container()
    if container.request_invite_otp_usecase is None:
        return _err(503, "ServiceUnavailable", "Invitation service not configured.")

    try:
        result = container.request_invite_otp_usecase.execute(data.token, data.phone)
    except InvalidInvitationTokenError:
        return _err(404, "NotFound", "Invitation not found.")
    except InvitationExpiredError as e:
        return _gone("expired", str(e))
    except InvitationRevokedError as e:
        return _gone("revoked", str(e))
    except InvitationAlreadyAcceptedError as e:
        return _gone("accepted", str(e))
    except InvalidPhoneNumberError as e:
        return _err(400, "ValidationError", str(e))
    except PhoneAlreadyRegisteredError:
        return _conflict("phone_registered", "This phone number already has an account.")
    except OtpThrottledError as e:
        return _err(429, "TooManyRequests", str(e))
    except SmsSendError:
        return _err(503, "ServiceUnavailable", "The SMS could not be sent. Try again later.")
    except Exception:
        return _err(500, "InternalError", "An unexpected error occurred.")

    from app import db

    db.session.commit()
    return jsonify(OtpRequestResponse(expires_in=result.expires_in).model_dump()), 202


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/accept  — public
# ---------------------------------------------------------------------------


@invitations_bp.route("/accept", methods=["POST"])
@openapi_doc(
    summary="Accept an invitation: create account + membership, return JWT cookies",
    request=AcceptInviteRequest,
    tags=["invitations"],
    auth=False,
)
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

    from app import db

    try:
        result = container.accept_invitation_usecase.execute(
            raw_token=data.token,
            name=data.name,
            phone=data.phone,
            code=data.code,
        )
    except InvalidInvitationTokenError:
        return _err(404, "NotFound", "Invitation not found.")
    except InvitationExpiredError as e:
        return _gone("expired", str(e))
    except InvitationRevokedError as e:
        return _gone("revoked", str(e))
    except InvitationAlreadyAcceptedError as e:
        return _gone("accepted", str(e))
    except InvalidPhoneNumberError as e:
        return _err(400, "ValidationError", str(e))
    except OtpInvalidError:
        # The attempt counter moved; persist it so guesses really are limited.
        db.session.commit()
        return _err(401, "Unauthorized", "Invalid or expired code")
    except PhoneAlreadyRegisteredError:
        db.session.commit()
        return _conflict("phone_registered", "This phone number already has an account.")
    except ValueError as e:
        return _err(422, "ValidationError", str(e))
    except Exception:
        return _err(500, "InternalError", "An unexpected error occurred.")

    # After the transaction: the inviter learns their invitation was taken up. A push
    # failure here must never undo an acceptance.
    notifier = container.membership_push_notifier
    if notifier is not None and result.invited_by is not None and result.project_id is not None:
        notifier.notify(
            "invitation_accepted",
            user_id=result.invited_by,
            actor_id=result.user.id,
            entity_id=result.project_id,
        )

    user_data = AcceptedUserResponse(
        id=result.user.id,
        email=result.user.email,
        display_name=result.user.display_name,
    )
    response = make_response(jsonify({"user": user_data.model_dump()}), 200)
    set_access_cookies(response, result.access_token)
    set_refresh_cookies(response, result.refresh_token)
    return response
