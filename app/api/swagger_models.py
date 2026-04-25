"""
Swagger API Models for Documentation

API models used for Swagger/OpenAPI documentation.
"""

from flask_restx import Api, fields


def create_auth_models(api: Api) -> dict:
    """Create authentication-related API models."""
    login_request = api.model(
        "LoginRequest",
        {
            "email": fields.String(required=True, description="User email", example="admin@example.com"),
            "password": fields.String(required=True, description="User password", example="password123"),
        },
    )

    user_response = api.model(
        "UserResponse",
        {
            "id": fields.String(description="User UUID", example="884c9aca-8c71-4229-b0ef-99946e938ac0"),
            "email": fields.String(description="User email", example="admin@example.com"),
            "permissions": fields.List(fields.String, description="User permissions", example=["*:*"]),
            "roles": fields.List(fields.String, description="User roles", example=["admin"]),
        },
    )

    login_response = api.model(
        "LoginResponse",
        {
            "access_token": fields.String(description="JWT access token"),
            "refresh_token": fields.String(description="JWT refresh token"),
            "token_type": fields.String(description="Token type", example="Bearer"),
            "expires_in": fields.Integer(description="Token expiry in seconds", example=1800),
            "user": fields.Nested(user_response),
        },
    )

    refresh_response = api.model(
        "RefreshResponse",
        {
            "access_token": fields.String(description="New JWT access token"),
            "token_type": fields.String(description="Token type", example="Bearer"),
            "expires_in": fields.Integer(description="Token expiry in seconds", example=1800),
        },
    )

    logout_response = api.model(
        "LogoutResponse",
        {
            "message": fields.String(description="Logout message", example="Successfully logged out"),
        },
    )

    return {
        "login_request": login_request,
        "user_response": user_response,
        "login_response": login_response,
        "refresh_response": refresh_response,
        "logout_response": logout_response,
    }


def create_common_models(api: Api) -> dict:
    """Create common API models (errors, health, etc.)."""
    error_response = api.model(
        "ErrorResponse",
        {
            "error": fields.String(description="Error code", example="Unauthorized"),
            "message": fields.String(description="Error message", example="Invalid email or password"),
            "status_code": fields.Integer(description="HTTP status code", example=401),
        },
    )

    health_response = api.model(
        "HealthResponse",
        {
            "status": fields.String(description="Health status", example="ok"),
        },
    )

    return {
        "error_response": error_response,
        "health_response": health_response,
    }


def create_project_models(api: Api) -> dict:
    """Create project-related API models."""
    create_project_request = api.model(
        "CreateProjectRequest",
        {
            "name": fields.String(required=True, description="Project name", example="Office Building A"),
            "address": fields.String(description="Project address", example="123 Main St"),
        },
    )

    update_project_request = api.model(
        "UpdateProjectRequest",
        {
            "name": fields.String(description="Project name", example="Office Building A"),
            "address": fields.String(description="Project address", example="123 Main St"),
        },
    )

    add_user_request = api.model(
        "AddUserRequest",
        {
            "user_id": fields.String(required=True, description="UUID of user to add"),
        },
    )

    project_response = api.model(
        "ProjectResponse",
        {
            "id": fields.String(description="Project UUID"),
            "name": fields.String(description="Project name"),
            "address": fields.String(description="Project address"),
            "owner_id": fields.String(description="Owner UUID"),
            "user_count": fields.Integer(description="Number of users"),
            "created_at": fields.String(description="Creation timestamp"),
        },
    )

    project_list_response = api.model(
        "ProjectListResponse",
        {
            "projects": fields.List(fields.Nested(project_response)),
            "total": fields.Integer(description="Total count"),
        },
    )

    message_response = api.model(
        "MessageResponse",
        {
            "message": fields.String(description="Response message"),
        },
    )

    return {
        "create_project_request": create_project_request,
        "update_project_request": update_project_request,
        "add_user_request": add_user_request,
        "project_response": project_response,
        "project_list_response": project_list_response,
        "message_response": message_response,
    }


def create_all_models(api: Api) -> dict:
    """Create all API models for Swagger documentation."""
    models = {}
    models.update(create_auth_models(api))
    models.update(create_common_models(api))
    models.update(create_project_models(api))
    return models
