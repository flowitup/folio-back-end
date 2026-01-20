"""
JWT Error Handlers

Configures Flask-JWT-Extended error callbacks for consistent error responses.
"""

from flask import jsonify
from flask_jwt_extended import JWTManager


def configure_jwt_handlers(jwt: JWTManager) -> None:
    """Configure JWT error handlers for the application."""

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({
            "error": "TokenExpired",
            "message": "Token has expired",
            "status_code": 401
        }), 401

    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({
            "error": "InvalidToken",
            "message": "Token is invalid",
            "status_code": 401
        }), 401

    @jwt.unauthorized_loader
    def missing_token_callback(error):
        return jsonify({
            "error": "Unauthorized",
            "message": "Missing authentication token",
            "status_code": 401
        }), 401

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        return jsonify({
            "error": "TokenRevoked",
            "message": "Token has been revoked",
            "status_code": 401
        }), 401

    @jwt.token_in_blocklist_loader
    def check_if_token_revoked(jwt_header, jwt_payload):
        jti = jwt_payload.get("jti")
        from wiring import get_container
        container = get_container()
        if container.token_issuer:
            return container.token_issuer.is_token_revoked(jti)
        return False
