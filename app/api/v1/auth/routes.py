"""Auth API routes."""

import logging
from functools import wraps
from typing import Any, Callable, TypeVar, cast
from uuid import UUID

from flask import current_app, jsonify, request, make_response
from flask_jwt_extended import (
    jwt_required,
    get_jwt_identity,
    get_jwt,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from pydantic import ValidationError

from app.api.openapi import openapi_doc
from app.api.v1.auth import auth_bp
from app.api.v1.auth.schemas import (
    AuthConfigResponse,
    LoginRequest,
    LoginResponse,
    LogoutBody,
    OtpRequestBody,
    OtpRequestResponse,
    OtpVerifyBody,
    RefreshResponse,
    SignupRequestBody,
    SignupVerifyBody,
    UserResponse,
    ErrorResponse,
    LogoutResponse,
)
from app.application.ports.sms_sender import SmsSendError
from app.application.usecases.login import LoginResult
from app.domain.exceptions.auth_exceptions import (
    InvalidCredentialsError,
    OtpInvalidError,
    OtpThrottledError,
    PhoneAlreadyRegisteredError,
    UserInactiveError,
    UserNotFoundError,
)
from app.domain.value_objects.phone_number import InvalidPhoneNumberError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _persistent_sessions() -> bool:
    return bool(current_app.config.get("REFRESH_TOKEN_POLICY", "expiring") == "persistent")


def require_login_mode(mode: str) -> Callable[[F], F]:
    """404 unless LOGIN_MODE allows this sign-in method ("both" allows everything)."""

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            allowed = current_app.config.get("LOGIN_MODE", "both")
            if allowed not in (mode, "both"):
                return (
                    jsonify(
                        ErrorResponse(
                            error="NotFound",
                            message=f"{mode.capitalize()} sign-in is not enabled on this server",
                            status_code=404,
                        ).model_dump()
                    ),
                    404,
                )
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


@auth_bp.route("/config", methods=["GET"])
@openapi_doc(
    summary="Sign-in options of this deployment", responses={200: AuthConfigResponse}, tags=["auth"], auth=False
)
def auth_config():
    """Public: which sign-in the apps should offer and whether sessions persist until sign-out."""
    return jsonify(
        AuthConfigResponse(
            login_mode=str(current_app.config.get("LOGIN_MODE", "both")),
            session="persistent" if _persistent_sessions() else "expiring",
            signup=current_app.config.get("LOGIN_MODE", "both") in ("phone", "both"),
        ).model_dump()
    )


@auth_bp.route("/login", methods=["POST"])
@openapi_doc(
    summary="Authenticate user and return tokens",
    request=LoginRequest,
    responses={200: LoginResponse},
    tags=["auth"],
    auth=False,
)
@limiter.limit("5 per minute")
@require_login_mode("email")
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
        result = container.login_usecase.execute(data.email, data.password, persistent=_persistent_sessions())
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

    return _login_response(container, result)


def _error(status: int, error: str, message: str):
    return jsonify(ErrorResponse(error=error, message=message, status_code=status).model_dump()), status


def _login_response(container, result: LoginResult):
    """200 body + auth cookies shared by password and SMS-code sign-in."""
    user = container.user_repository.find_by_id(result.user_id)
    response_data = LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            permissions=result.permissions,
            roles=[r.name for r in user.roles],
            phone=user.phone,
        ),
    )
    response = make_response(jsonify(response_data.model_dump()))
    # Set cookies for browser clients
    set_access_cookies(response, result.access_token)
    set_refresh_cookies(response, result.refresh_token)
    return response


@auth_bp.route("/otp/request", methods=["POST"])
@openapi_doc(
    summary="Send a 6-digit sign-in code by SMS to a phone number",
    request=OtpRequestBody,
    responses={202: OtpRequestResponse},
    tags=["auth"],
    auth=False,
)
@limiter.limit("5 per minute")
@require_login_mode("phone")
def request_otp():
    """Always answers 202 for a well-formed number, whether or not an account has it."""
    try:
        data = OtpRequestBody(**(request.get_json(silent=True) or {}))
    except ValidationError:
        return _error(400, "ValidationError", "Invalid input: phone")
    container = get_container()
    if container.request_otp_usecase is None:
        return _error(500, "ServerError", "SMS sign-in not configured")
    try:
        result = container.request_otp_usecase.execute(data.phone)
    except InvalidPhoneNumberError:
        return _error(400, "ValidationError", "Invalid phone number")
    except OtpThrottledError:
        return _error(429, "TooManyRequests", "A code was sent recently. Wait a minute and try again.")
    except SmsSendError:
        return _error(503, "ServiceUnavailable", "The SMS could not be sent. Try again later.")
    from app import db

    db.session.commit()
    return jsonify(OtpRequestResponse(expires_in=result.expires_in).model_dump()), 202


@auth_bp.route("/otp/verify", methods=["POST"])
@openapi_doc(
    summary="Exchange a phone number + SMS code for tokens",
    request=OtpVerifyBody,
    responses={200: LoginResponse},
    tags=["auth"],
    auth=False,
)
@limiter.limit("5 per minute")
@require_login_mode("phone")
def verify_otp():
    try:
        data = OtpVerifyBody(**(request.get_json(silent=True) or {}))
    except ValidationError:
        return _error(400, "ValidationError", "Invalid input: phone, code")
    container = get_container()
    if container.verify_otp_usecase is None:
        return _error(500, "ServerError", "SMS sign-in not configured")
    from app import db

    try:
        result = container.verify_otp_usecase.execute(data.phone, data.code, persistent=_persistent_sessions())
    except InvalidPhoneNumberError:
        return _error(400, "ValidationError", "Invalid phone number")
    except (OtpInvalidError, UserInactiveError):
        # The attempt counter moved; persist it so guesses really are limited.
        db.session.commit()
        return _error(401, "Unauthorized", "Invalid or expired code")
    db.session.commit()
    return _login_response(container, result)


@auth_bp.route("/logout", methods=["POST"])
@openapi_doc(
    summary="Logout user, clear cookies, revoke the access and refresh tokens", request=LogoutBody, tags=["auth"]
)
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

    # Also revoke the refresh token so it cannot be replayed after the user logs out:
    # browsers carry it in the refresh cookie, the mobile app sends it in the JSON body
    # (its persistent refresh tokens never expire, so this revocation is what ends the session).
    # Decoded with allow_expired because flask-jwt-extended only treats one token kind per
    # request and logout must not fail when the token is missing or already expired.
    if token_issuer:
        _cookie_name = current_app.config.get("JWT_REFRESH_COOKIE_NAME", "refresh_token_cookie")
        candidates = [request.cookies.get(_cookie_name)]
        try:
            body = LogoutBody(**(request.get_json(silent=True) or {}))
            candidates.append(body.refresh_token)
        except ValidationError:
            pass
        for refresh_token in candidates:
            if not refresh_token:
                continue
            try:
                from flask_jwt_extended import decode_token

                refresh_claims = decode_token(refresh_token, allow_expired=True)
                refresh_jti = refresh_claims.get("jti") if refresh_claims else None
                if refresh_jti:
                    token_issuer.revoke_token(
                        refresh_jti, token_type="refresh", persistent=bool(refresh_claims.get("persistent"))
                    )
            except Exception:  # pragma: no cover - defensive; logout must not 500
                logger.info("auth.logout: refresh-token decode failed; access JTI still revoked")

    return response


@auth_bp.route("/refresh", methods=["POST"])
@openapi_doc(
    summary="Refresh access token using refresh token",
    responses={200: RefreshResponse},
    tags=["auth"],
    auth=False,
)
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
@openapi_doc(summary="Get current authenticated user info", responses={200: UserResponse}, tags=["auth"])
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
            phone=user.phone,
        ).model_dump()
    )


@auth_bp.route("/signup/request", methods=["POST"])
@openapi_doc(
    summary="Text a sign-up code to a phone number that has no account yet",
    request=SignupRequestBody,
    responses={202: OtpRequestResponse},
    tags=["auth"],
    auth=False,
)
@limiter.limit("5 per minute")
@require_login_mode("phone")
def request_signup_otp():
    try:
        data = SignupRequestBody(**(request.get_json(silent=True) or {}))
    except ValidationError:
        return _error(400, "ValidationError", "Invalid input: phone")
    container = get_container()
    if container.request_signup_otp_usecase is None:
        return _error(500, "ServerError", "Sign-up not configured")
    try:
        result = container.request_signup_otp_usecase.execute(data.phone)
    except InvalidPhoneNumberError:
        return _error(400, "ValidationError", "Invalid phone number")
    except PhoneAlreadyRegisteredError:
        return _error(409, "Conflict", "This phone number already has an account. Sign in instead.")
    except OtpThrottledError:
        return _error(429, "TooManyRequests", "A code was sent recently. Wait a minute and try again.")
    except SmsSendError:
        return _error(503, "ServiceUnavailable", "The SMS could not be sent. Try again later.")
    from app import db

    db.session.commit()
    return jsonify(OtpRequestResponse(expires_in=result.expires_in).model_dump()), 202


@auth_bp.route("/signup/verify", methods=["POST"])
@openapi_doc(
    summary="Create an account from a phone number + sign-up code + display name",
    request=SignupVerifyBody,
    responses={201: LoginResponse},
    tags=["auth"],
    auth=False,
)
@limiter.limit("5 per minute")
@require_login_mode("phone")
def verify_signup_otp():
    try:
        data = SignupVerifyBody(**(request.get_json(silent=True) or {}))
    except ValidationError:
        return _error(400, "ValidationError", "Invalid input: phone, code, display_name")
    container = get_container()
    if container.verify_signup_otp_usecase is None:
        return _error(500, "ServerError", "Sign-up not configured")
    from app import db

    try:
        result = container.verify_signup_otp_usecase.execute(
            data.phone, data.code, data.display_name, persistent=_persistent_sessions()
        )
    except InvalidPhoneNumberError:
        return _error(400, "ValidationError", "Invalid phone number")
    except OtpInvalidError:
        db.session.commit()
        return _error(401, "Unauthorized", "Invalid or expired code")
    except PhoneAlreadyRegisteredError:
        db.session.commit()
        return _error(409, "Conflict", "This phone number already has an account. Sign in instead.")
    db.session.commit()
    response = _login_response(container, result)
    response.status_code = 201
    return response
