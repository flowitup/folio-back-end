"""Project API routes."""

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from pydantic import ValidationError

from app.api.v1.projects import projects_bp
from app.api.v1.projects.schemas import (
    CreateProjectRequest, UpdateProjectRequest, AddUserRequest,
    ProjectResponse, ProjectListResponse, ErrorResponse
)
from app.api.v1.projects.decorators import require_permission, has_permission
from app.application.projects import CreateProjectRequest as CreateDTO
from app.domain.exceptions.project_exceptions import (
    ProjectNotFoundError, InvalidProjectDataError
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


@projects_bp.route("", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def list_projects():
    """List projects for current user (or all if admin)."""
    container = get_container()
    user_id = get_jwt_identity()
    is_admin = has_permission("project:create")

    projects = container.list_projects_usecase.execute(
        UUID(user_id), is_admin=is_admin
    )

    return jsonify(ProjectListResponse(
        projects=[
            ProjectResponse(
                id=p.id,
                name=p.name,
                address=p.address,
                owner_id=p.owner_id,
                user_count=p.user_count,
                created_at=""
            ) for p in projects
        ],
        total=len(projects)
    ).model_dump())


@projects_bp.route("", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:create")
def create_project():
    """Create a new project."""
    try:
        data = CreateProjectRequest(**request.get_json())
    except ValidationError as e:
        error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
        return jsonify(ErrorResponse(
            error="ValidationError",
            message=f"Invalid input: {', '.join(str(f) for f in error_fields)}",
            status_code=400
        ).model_dump()), 400

    container = get_container()
    user_id = get_jwt_identity()

    try:
        result = container.create_project_usecase.execute(CreateDTO(
            name=data.name,
            address=data.address,
            owner_id=UUID(user_id)
        ))
    except InvalidProjectDataError as e:
        return jsonify(ErrorResponse(
            error="ValidationError",
            message=str(e),
            status_code=400
        ).model_dump()), 400

    return jsonify(ProjectResponse(
        id=result.id,
        name=result.name,
        address=result.address,
        owner_id=result.owner_id,
        user_count=0,
        created_at=result.created_at
    ).model_dump()), 201


@projects_bp.route("/<project_id>", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def get_project(project_id: str):
    """Get a single project by ID."""
    container = get_container()

    try:
        project = container.get_project_usecase.execute(UUID(project_id))
    except ProjectNotFoundError:
        return jsonify(ErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            status_code=404
        ).model_dump()), 404

    return jsonify(ProjectResponse(
        id=str(project.id),
        name=project.name,
        address=project.address,
        owner_id=str(project.owner_id),
        user_count=len(project.user_ids),
        created_at=project.created_at.isoformat()
    ).model_dump())


@projects_bp.route("/<project_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:update")
def update_project(project_id: str):
    """Update an existing project."""
    try:
        data = UpdateProjectRequest(**request.get_json())
    except ValidationError as e:
        error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
        return jsonify(ErrorResponse(
            error="ValidationError",
            message=f"Invalid input: {', '.join(str(f) for f in error_fields)}",
            status_code=400
        ).model_dump()), 400

    container = get_container()

    try:
        result = container.update_project_usecase.execute(
            UUID(project_id),
            name=data.name,
            address=data.address
        )
    except ProjectNotFoundError:
        return jsonify(ErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            status_code=404
        ).model_dump()), 404
    except InvalidProjectDataError as e:
        return jsonify(ErrorResponse(
            error="ValidationError",
            message=str(e),
            status_code=400
        ).model_dump()), 400

    return jsonify(ProjectResponse(
        id=str(result.id),
        name=result.name,
        address=result.address,
        owner_id=str(result.owner_id),
        user_count=len(result.user_ids),
        created_at=result.created_at.isoformat()
    ).model_dump())


@projects_bp.route("/<project_id>", methods=["DELETE"])
@jwt_required()
@limiter.limit("5 per minute")
@require_permission("project:delete")
def delete_project(project_id: str):
    """Delete a project."""
    container = get_container()

    try:
        container.delete_project_usecase.execute(UUID(project_id))
    except ProjectNotFoundError:
        return jsonify(ErrorResponse(
            error="NotFound",
            message=f"Project {project_id} not found",
            status_code=404
        ).model_dump()), 404

    return "", 204


@projects_bp.route("/<project_id>/users", methods=["POST"])
@jwt_required()
@require_permission("project:manage_users")
def add_user_to_project(project_id: str):
    """Add a user to a project."""
    try:
        data = AddUserRequest(**request.get_json())
    except ValidationError as e:
        error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
        return jsonify(ErrorResponse(
            error="ValidationError",
            message=f"Invalid input: {', '.join(str(f) for f in error_fields)}",
            status_code=400
        ).model_dump()), 400

    container = get_container()
    container.project_repository.add_user(UUID(project_id), UUID(data.user_id))

    return jsonify({"message": "User added to project"}), 200


@projects_bp.route("/<project_id>/users/<user_id>", methods=["DELETE"])
@jwt_required()
@require_permission("project:manage_users")
def remove_user_from_project(project_id: str, user_id: str):
    """Remove a user from a project."""
    container = get_container()
    container.project_repository.remove_user(UUID(project_id), UUID(user_id))

    return "", 204
