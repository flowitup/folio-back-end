"""Auth API routes."""

from uuid import UUID

from flask import jsonify, request, make_response
from flask_jwt_extended import (
    jwt_required, get_jwt_identity, get_jwt,
    set_access_cookies, set_refresh_cookies, unset_jwt_cookies
)
from pydantic import ValidationError

from app.api.v1.auth import auth_bp
from app.api.v1.auth.schemas import (
    LoginRequest, LoginResponse, RefreshResponse,
    UserResponse, ErrorResponse, LogoutResponse
)
from app.domain.exceptions.auth_exceptions import (
    InvalidCredentialsError, UserNotFoundError, UserInactiveError
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


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
        return jsonify(ErrorResponse(
            error="ValidationError",
            message=str(e),
            status_code=400
        ).model_dump()), 400

    container = get_container()

    # Use container's LoginUseCase
    if not container.login_usecase:
        return jsonify(ErrorResponse(
            error="ServerError",
            message="Auth services not configured",
            status_code=500
        ).model_dump()), 500

    try:
        result = container.login_usecase.execute(data.email, data.password)
    except (InvalidCredentialsError, UserNotFoundError):
        return jsonify(ErrorResponse(
            error="Unauthorized",
            message="Invalid email or password",
            status_code=401
        ).model_dump()), 401
    except UserInactiveError:
        return jsonify(ErrorResponse(
            error="Forbidden",
            message="Account is deactivated",
            status_code=403
        ).model_dump()), 403

    # Get user for response
    user = container.user_repository.find_by_id(result.user_id)

    response_data = LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            permissions=result.permissions,
            roles=[r.name for r in user.roles]
        )
    )

    response = make_response(jsonify(response_data.model_dump()))

    # Set cookies for browser clients
    set_access_cookies(response, result.access_token)
    set_refresh_cookies(response, result.refresh_token)

    return response


@auth_bp.route("/logout", methods=["POST"])
@jwt_required(optional=True)
def logout():
    """Logout user - clear cookies and optionally revoke token."""
    response = make_response(jsonify(LogoutResponse().model_dump()))
    unset_jwt_cookies(response)

    # Revoke token if present
    jwt_data = get_jwt()
    if jwt_data:
        jti = jwt_data.get("jti")
        if jti:
            container = get_container()
            if container.token_issuer:
                container.token_issuer.revoke_token(jti)

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
    new_access_token = container.token_issuer.create_access_token(
        UUID(user_id),
        {"permissions": permissions}
    )

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
        return jsonify(ErrorResponse(
            error="NotFound",
            message="User not found",
            status_code=404
        ).model_dump()), 404

    return jsonify(UserResponse(
        id=user.id,
        email=user.email,
        permissions=jwt_claims.get("permissions", []),
        roles=[r.name for r in user.roles]
    ).model_dump())
