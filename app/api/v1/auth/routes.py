"""Auth API routes."""

import logging
from uuid import UUID

from flask import jsonify, request, make_response
from flask_jwt_extended import (
    jwt_required,
    get_jwt_identity,
    get_jwt,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from pydantic import ValidationError

from app.api.v1.auth import auth_bp
from app.api.v1.auth.schemas import (
    LoginRequest,
    LoginResponse,
    RefreshResponse,
    UserResponse,
    ErrorResponse,
    LogoutResponse,
)
from app.domain.exceptions.auth_exceptions import InvalidCredentialsError, UserNotFoundError, UserInactiveError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute")
def login():
    """
    Authenticate user and return tokens.

    Request: { "email": "user@example.com", "password": "********" }
    Response: { "access_token": "...", "refresh_token": "...", "user": {...} }
    """
    try:
        data = LoginRequest(**request.get_json())
    except ValidationError as e:
        # Sanitize Pydantic errors - don't expose internals
        error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
        return (
            jsonify(
                ErrorResponse(
                    error="ValidationError",
                    message=f"Invalid input: {', '.join(str(f) for f in error_fields)}",
                    status_code=400,
                ).model_dump()
            ),
            400,
        )

    container = get_container()

    # Use container's LoginUseCase
    if not container.login_usecase:
        return (
            jsonify(
                ErrorResponse(error="ServerError", message="Auth services not configured", status_code=500).model_dump()
            ),
            500,
        )

    # Normalize all login-failure paths to a single 401 response so attackers cannot
    # distinguish "user does not exist" / "wrong password" / "account deactivated"
    # via status code or body. Deactivated-account UX (a friendlier message) is
    # surfaced post-authentication via the dedicated user-status flow, never on
    # the unauthenticated /login endpoint.
    try:
        result = container.login_usecase.execute(data.email, data.password)
    except (InvalidCredentialsError, UserNotFoundError):
        return (
            jsonify(
                ErrorResponse(error="Unauthorized", message="Invalid email or password", status_code=401).model_dump()
            ),
            401,
        )
    except UserInactiveError:
        # Emit a separate ops-side signal for visibility without leaking via HTTP.
        logger.info("auth.login.deactivated_attempt email=%s", data.email)
        return (
            jsonify(
                ErrorResponse(error="Unauthorized", message="Invalid email or password", status_code=401).model_dump()
            ),
            401,
        )

    # Get user for response
    user = container.user_repository.find_by_id(result.user_id)

    response_data = LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user=UserResponse(
            id=user.id, email=user.email, permissions=result.permissions, roles=[r.name for r in user.roles]
        ),
    )

    response = make_response(jsonify(response_data.model_dump()))

    # Set cookies for browser clients
    set_access_cookies(response, result.access_token)
    set_refresh_cookies(response, result.refresh_token)

    return response


@auth_bp.route("/logout", methods=["POST"])
@jwt_required(optional=True)
def logout():
    """Logout user - clear cookies and revoke both access and refresh tokens."""
    response = make_response(jsonify(LogoutResponse().model_dump()))
    unset_jwt_cookies(response)

    container = get_container()
    token_issuer = container.token_issuer if container else None

    # Revoke the presented access token (if any).
    jwt_data = get_jwt()
    if jwt_data and token_issuer:
        jti = jwt_data.get("jti")
        if jti:
            token_issuer.revoke_token(jti, token_type="access")

    # Also revoke the refresh-token JTI carried in the refresh cookie so a
    # captured refresh token cannot be replayed after the user logs out.
    # The refresh cookie is decoded with verify=False because flask-jwt-extended
    # only treats one token kind per request and we don't want to fail logout
    # when the refresh cookie is missing or already expired.
    if token_issuer:
        refresh_cookie = request.cookies.get("refresh_token_cookie")
        if refresh_cookie:
            try:
                from flask_jwt_extended import decode_token

                refresh_claims = decode_token(refresh_cookie, allow_expired=True)
                refresh_jti = refresh_claims.get("jti") if refresh_claims else None
                if refresh_jti:
                    token_issuer.revoke_token(refresh_jti, token_type="refresh")
            except Exception:  # pragma: no cover - defensive; logout must not 500
                logger.info("auth.logout: refresh-token decode failed; access JTI still revoked")

    return response


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """Refresh access token using refresh token."""
    user_id = get_jwt_identity()
    container = get_container()

    # Get fresh permissions
    permissions = list(container.authorization_service.get_user_permissions(UUID(user_id)))

    # Create new access token
    new_access_token = container.token_issuer.create_access_token(UUID(user_id), {"permissions": permissions})

    response_data = RefreshResponse(access_token=new_access_token)
    response = make_response(jsonify(response_data.model_dump()))
    set_access_cookies(response, new_access_token)

    return response


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def get_current_user():
    """Get current authenticated user info."""
    user_id = get_jwt_identity()
    jwt_claims = get_jwt()

    container = get_container()
    user = container.user_repository.find_by_id(UUID(user_id))

    if not user:
        return jsonify(ErrorResponse(error="NotFound", message="User not found", status_code=404).model_dump()), 404

    return jsonify(
        UserResponse(
            id=user.id,
            email=user.email,
            permissions=jwt_claims.get("permissions", []),
            roles=[r.name for r in user.roles],
        ).model_dump()
    )
