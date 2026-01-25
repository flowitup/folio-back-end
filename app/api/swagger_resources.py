"""
Flask-RESTX Resources for Swagger Documentation

These resources delegate to existing Flask blueprint routes.
They exist solely for Swagger documentation purposes.
"""

from uuid import UUID

from flask import request, make_response, jsonify
from flask_restx import Resource, Namespace
from flask_jwt_extended import (
    jwt_required, get_jwt_identity, get_jwt,
    set_access_cookies, set_refresh_cookies, unset_jwt_cookies
)
from pydantic import ValidationError


def register_resources(auth_ns: Namespace, models: dict) -> None:
    """Register all auth resources with the namespace."""
    login_request_model = models["login_request"]
    login_response_model = models["login_response"]
    user_response_model = models["user_response"]
    refresh_response_model = models["refresh_response"]
    logout_response_model = models["logout_response"]
    error_response_model = models["error_response"]

    @auth_ns.route("/login")
    class LoginResource(Resource):
        """Login endpoint for user authentication."""

        @auth_ns.doc("login", security=None)
        @auth_ns.expect(login_request_model)
        @auth_ns.response(200, "Success", login_response_model)
        @auth_ns.response(400, "Validation Error", error_response_model)
        @auth_ns.response(401, "Invalid Credentials", error_response_model)
        def post(self):
            """Authenticate user and return tokens."""
            from app.api.v1.auth.schemas import LoginRequest, LoginResponse, UserResponse, ErrorResponse
            from app.domain.exceptions.auth_exceptions import InvalidCredentialsError, UserNotFoundError, UserInactiveError
            from wiring import get_container

            try:
                data = LoginRequest(**request.get_json())
            except ValidationError as e:
                error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
                return {"error": "ValidationError", "message": f"Invalid: {', '.join(str(f) for f in error_fields)}", "status_code": 400}, 400

            container = get_container()
            if not container.login_usecase:
                return {"error": "ServerError", "message": "Auth not configured", "status_code": 500}, 500

            try:
                result = container.login_usecase.execute(data.email, data.password)
            except (InvalidCredentialsError, UserNotFoundError):
                return {"error": "Unauthorized", "message": "Invalid email or password", "status_code": 401}, 401
            except UserInactiveError:
                return {"error": "Forbidden", "message": "Account deactivated", "status_code": 403}, 403

            user = container.user_repository.find_by_id(result.user_id)
            response_data = LoginResponse(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                user=UserResponse(id=user.id, email=user.email, permissions=result.permissions, roles=[r.name for r in user.roles])
            )
            response = make_response(jsonify(response_data.model_dump()))
            set_access_cookies(response, result.access_token)
            set_refresh_cookies(response, result.refresh_token)
            return response

    @auth_ns.route("/logout")
    class LogoutResource(Resource):
        """Logout endpoint."""

        @auth_ns.doc("logout")
        @auth_ns.response(200, "Success", logout_response_model)
        @jwt_required(optional=True)
        def post(self):
            """Logout user and revoke token."""
            from app.api.v1.auth.schemas import LogoutResponse
            from wiring import get_container

            response = make_response(jsonify(LogoutResponse().model_dump()))
            unset_jwt_cookies(response)
            jwt_data = get_jwt()
            if jwt_data and (jti := jwt_data.get("jti")):
                container = get_container()
                if container.token_issuer:
                    container.token_issuer.revoke_token(jti)
            return response

    @auth_ns.route("/refresh")
    class RefreshResource(Resource):
        """Token refresh endpoint."""

        @auth_ns.doc("refresh")
        @auth_ns.response(200, "Success", refresh_response_model)
        @auth_ns.response(401, "Invalid Token", error_response_model)
        @jwt_required(refresh=True)
        def post(self):
            """Refresh access token."""
            from app.api.v1.auth.schemas import RefreshResponse
            from wiring import get_container

            user_id = get_jwt_identity()
            container = get_container()
            permissions = list(container.authorization_service.get_user_permissions(UUID(user_id)))
            new_token = container.token_issuer.create_access_token(UUID(user_id), {"permissions": permissions})
            response = make_response(jsonify(RefreshResponse(access_token=new_token).model_dump()))
            set_access_cookies(response, new_token)
            return response

    @auth_ns.route("/me")
    class MeResource(Resource):
        """Current user endpoint."""

        @auth_ns.doc("get_current_user")
        @auth_ns.response(200, "Success", user_response_model)
        @auth_ns.response(401, "Unauthorized", error_response_model)
        @jwt_required()
        def get(self):
            """Get current authenticated user."""
            from app.api.v1.auth.schemas import UserResponse, ErrorResponse
            from wiring import get_container

            user_id = get_jwt_identity()
            container = get_container()
            user = container.user_repository.find_by_id(UUID(user_id))
            if not user:
                return {"error": "NotFound", "message": "User not found", "status_code": 404}, 404
            return UserResponse(id=user.id, email=user.email, permissions=get_jwt().get("permissions", []), roles=[r.name for r in user.roles]).model_dump()


def register_project_resources(projects_ns: Namespace, models: dict) -> None:
    """Register all project resources with the namespace."""
    create_project_model = models["create_project_request"]
    update_project_model = models["update_project_request"]
    add_user_model = models["add_user_request"]
    project_response_model = models["project_response"]
    project_list_model = models["project_list_response"]
    message_model = models["message_response"]
    error_model = models["error_response"]

    @projects_ns.route("")
    class ProjectListResource(Resource):
        """Project list and creation endpoint."""

        @projects_ns.doc("list_projects")
        @projects_ns.response(200, "Success", project_list_model)
        @projects_ns.response(401, "Unauthorized", error_model)
        @jwt_required()
        def get(self):
            """List projects for current user."""
            from app.api.v1.projects.routes import list_projects
            return list_projects()

        @projects_ns.doc("create_project")
        @projects_ns.expect(create_project_model)
        @projects_ns.response(201, "Created", project_response_model)
        @projects_ns.response(400, "Validation Error", error_model)
        @projects_ns.response(401, "Unauthorized", error_model)
        @jwt_required()
        def post(self):
            """Create a new project."""
            from app.api.v1.projects.routes import create_project
            return create_project()

    @projects_ns.route("/<project_id>")
    @projects_ns.param("project_id", "Project UUID")
    class ProjectResource(Resource):
        """Single project operations."""

        @projects_ns.doc("get_project")
        @projects_ns.response(200, "Success", project_response_model)
        @projects_ns.response(404, "Not Found", error_model)
        @projects_ns.response(401, "Unauthorized", error_model)
        @jwt_required()
        def get(self, project_id: str):
            """Get a project by ID."""
            from app.api.v1.projects.routes import get_project
            return get_project(project_id)

        @projects_ns.doc("update_project")
        @projects_ns.expect(update_project_model)
        @projects_ns.response(200, "Success", project_response_model)
        @projects_ns.response(400, "Validation Error", error_model)
        @projects_ns.response(404, "Not Found", error_model)
        @projects_ns.response(401, "Unauthorized", error_model)
        @jwt_required()
        def put(self, project_id: str):
            """Update a project."""
            from app.api.v1.projects.routes import update_project
            return update_project(project_id)

        @projects_ns.doc("delete_project")
        @projects_ns.response(204, "Deleted")
        @projects_ns.response(404, "Not Found", error_model)
        @projects_ns.response(401, "Unauthorized", error_model)
        @jwt_required()
        def delete(self, project_id: str):
            """Delete a project."""
            from app.api.v1.projects.routes import delete_project
            return delete_project(project_id)

    @projects_ns.route("/<project_id>/users")
    @projects_ns.param("project_id", "Project UUID")
    class ProjectUsersResource(Resource):
        """Project user management."""

        @projects_ns.doc("add_user_to_project")
        @projects_ns.expect(add_user_model)
        @projects_ns.response(200, "Success", message_model)
        @projects_ns.response(400, "Validation Error", error_model)
        @projects_ns.response(401, "Unauthorized", error_model)
        @jwt_required()
        def post(self, project_id: str):
            """Add a user to a project."""
            from app.api.v1.projects.routes import add_user_to_project
            return add_user_to_project(project_id)

    @projects_ns.route("/<project_id>/users/<user_id>")
    @projects_ns.param("project_id", "Project UUID")
    @projects_ns.param("user_id", "User UUID")
    class ProjectUserResource(Resource):
        """Single user in project operations."""

        @projects_ns.doc("remove_user_from_project")
        @projects_ns.response(204, "Deleted")
        @projects_ns.response(401, "Unauthorized", error_model)
        @jwt_required()
        def delete(self, project_id: str, user_id: str):
            """Remove a user from a project."""
            from app.api.v1.projects.routes import remove_user_from_project
            return remove_user_from_project(project_id, user_id)
