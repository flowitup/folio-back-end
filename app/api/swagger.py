"""
Flask-RESTX Swagger API Configuration

Provides Swagger UI documentation at /v1/documentation with interactive API explorer.
Models defined here are for documentation purposes only - actual routes use Flask blueprints.
"""

from flask import Flask
from flask_restx import Api, fields

# Create API instance with Swagger configuration
api = Api(
    title="Construction Management API",
    version="0.2.0",
    description="Backend API for Construction Management System",
    doc="/v1/documentation",
    authorizations={
        "Bearer": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "JWT Bearer token. Format: 'Bearer {token}'"
        }
    },
    security="Bearer",
)

# Namespace for Auth endpoints (documentation only)
auth_ns = api.namespace("auth", description="Authentication operations", path="/api/v1/auth")

# ============================================================================
# API Models for Swagger Documentation
# ============================================================================

login_request_model = api.model("LoginRequest", {
    "email": fields.String(required=True, description="User email", example="admin@example.com"),
    "password": fields.String(required=True, description="User password", example="password123"),
})

user_response_model = api.model("UserResponse", {
    "id": fields.String(description="User UUID", example="884c9aca-8c71-4229-b0ef-99946e938ac0"),
    "email": fields.String(description="User email", example="admin@example.com"),
    "permissions": fields.List(fields.String, description="User permissions", example=["*:*"]),
    "roles": fields.List(fields.String, description="User roles", example=["admin"]),
})

login_response_model = api.model("LoginResponse", {
    "access_token": fields.String(description="JWT access token"),
    "refresh_token": fields.String(description="JWT refresh token"),
    "token_type": fields.String(description="Token type", example="Bearer"),
    "expires_in": fields.Integer(description="Token expiry in seconds", example=1800),
    "user": fields.Nested(user_response_model),
})

refresh_response_model = api.model("RefreshResponse", {
    "access_token": fields.String(description="New JWT access token"),
    "token_type": fields.String(description="Token type", example="Bearer"),
    "expires_in": fields.Integer(description="Token expiry in seconds", example=1800),
})

logout_response_model = api.model("LogoutResponse", {
    "message": fields.String(description="Logout message", example="Successfully logged out"),
})

error_response_model = api.model("ErrorResponse", {
    "error": fields.String(description="Error code", example="Unauthorized"),
    "message": fields.String(description="Error message", example="Invalid email or password"),
    "status_code": fields.Integer(description="HTTP status code", example=401),
})

health_response_model = api.model("HealthResponse", {
    "status": fields.String(description="Health status", example="ok"),
})


def init_swagger(app: Flask) -> None:
    """
    Initialize Swagger API documentation on the Flask app.

    Note: This registers Flask-RESTX for Swagger UI only.
    Actual routes are handled by Flask blueprints in app/api/v1/.
    """
    # Import resources to register them with namespaces
    from app.api.swagger_resources import register_resources
    register_resources(auth_ns)

    api.init_app(app)
