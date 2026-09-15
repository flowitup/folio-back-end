"""
JWT Error Handlers

Configures Flask-JWT-Extended error callbacks for consistent error responses.
"""

from uuid import UUID

from flask import jsonify
from flask_jwt_extended import JWTManager


def configure_jwt_handlers(jwt: JWTManager) -> None:
    """Configure JWT error handlers for the application."""

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({"error": "TokenExpired", "message": "Token has expired", "status_code": 401}), 401

    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({"error": "InvalidToken", "message": "Token is invalid", "status_code": 401}), 401

    @jwt.unauthorized_loader
    def missing_token_callback(error):
        return jsonify({"error": "Unauthorized", "message": "Missing authentication token", "status_code": 401}), 401

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        return jsonify({"error": "TokenRevoked", "message": "Token has been revoked", "status_code": 401}), 401

    @jwt.token_in_blocklist_loader
    def check_if_token_revoked(jwt_header, jwt_payload):
        """Reject the token if it was revoked, or if its user can no longer sign in.

        The user check is what makes account deletion actually end a session.
        Revocation is per-JTI, so it only reaches tokens the server has seen: a
        refresh token sitting on a second device is never presented at deletion
        time, and mobile refresh tokens do not expire. Without this, an erased
        account could keep minting access tokens indefinitely. Checking here
        rather than in /auth/refresh alone also invalidates access tokens already
        issued, instead of leaving a window until they expire.

        Costs one primary-key lookup per authenticated request. Deliberately not
        cached: ``flask.g`` is bound to the application context, not the request,
        so any caller holding one open across requests would serve a stale
        "still active" verdict — exactly the failure this check exists to prevent.
        """
        jti = jwt_payload.get("jti")
        from wiring import get_container

        container = get_container()
        if container.token_issuer and container.token_issuer.is_token_revoked(jti):
            return True
        return not _token_subject_may_sign_in(container, jwt_payload.get("sub"))


def _token_subject_may_sign_in(container, subject) -> bool:
    """True when the token's subject is a user that still exists and is active.

    Fails open only when there is no user repository to ask (unit-test containers
    that wire nothing) — never when the repository answers "no such user".
    """
    repository = getattr(container, "user_repository", None)
    check = getattr(repository, "is_sign_in_allowed", None)
    if check is None or not subject:
        return True

    try:
        return bool(check(UUID(str(subject))))
    except ValueError:
        # Not a UUID subject: let the normal token validation reject it.
        return True
