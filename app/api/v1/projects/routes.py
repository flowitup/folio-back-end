"""Project API routes."""

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from pydantic import ValidationError

from app.api.v1.projects import projects_bp
from app.api.v1.projects.schemas import (
    CreateProjectRequest,
    UpdateProjectRequest,
    AddUserRequest,
    ProjectResponse,
    ProjectListResponse,
    ErrorResponse,
    ProjectUserResponse,
    ProjectUsersListResponse,
)
from app.api.v1.projects.decorators import require_permission, has_permission, can_read_project, can_mutate_project
from app.application.projects import CreateProjectRequest as CreateDTO
from app.domain.exceptions.project_exceptions import ProjectNotFoundError, InvalidProjectDataError
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

    projects = container.list_projects_usecase.execute(UUID(user_id), is_admin=is_admin)

    return jsonify(
        ProjectListResponse(
            projects=[
                ProjectResponse(
                    id=p.id, name=p.name, address=p.address, owner_id=p.owner_id, user_count=p.user_count, created_at=""
                )
                for p in projects
            ],
            total=len(projects),
        ).model_dump()
    )


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
    user_id = get_jwt_identity()

    try:
        result = container.create_project_usecase.execute(
            CreateDTO(name=data.name, address=data.address, owner_id=UUID(user_id))
        )
    except InvalidProjectDataError as e:
        return jsonify(ErrorResponse(error="ValidationError", message=str(e), status_code=400).model_dump()), 400

    return (
        jsonify(
            ProjectResponse(
                id=result.id,
                name=result.name,
                address=result.address,
                owner_id=result.owner_id,
                user_count=0,
                created_at=result.created_at,
            ).model_dump()
        ),
        201,
    )


@projects_bp.route("/<project_id>", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def get_project(project_id: str):
    """Get a single project by ID."""
    container = get_container()
    user_id = UUID(get_jwt_identity())

    try:
        project = container.get_project_usecase.execute(UUID(project_id))
    except ProjectNotFoundError:
        return (
            jsonify(
                ErrorResponse(error="NotFound", message=f"Project {project_id} not found", status_code=404).model_dump()
            ),
            404,
        )

    if not can_read_project(project, user_id):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    return jsonify(
        ProjectResponse(
            id=str(project.id),
            name=project.name,
            address=project.address,
            owner_id=str(project.owner_id),
            user_count=len(project.user_ids),
            created_at=project.created_at.isoformat(),
        ).model_dump()
    )


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
    user_id = UUID(get_jwt_identity())

    # Load project first to check ownership before mutating
    try:
        existing = container.get_project_usecase.execute(UUID(project_id))
    except ProjectNotFoundError:
        return (
            jsonify(
                ErrorResponse(error="NotFound", message=f"Project {project_id} not found", status_code=404).model_dump()
            ),
            404,
        )

    if not can_mutate_project(existing, user_id):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    try:
        result = container.update_project_usecase.execute(UUID(project_id), name=data.name, address=data.address)
    except InvalidProjectDataError as e:
        return jsonify(ErrorResponse(error="ValidationError", message=str(e), status_code=400).model_dump()), 400

    return jsonify(
        ProjectResponse(
            id=str(result.id),
            name=result.name,
            address=result.address,
            owner_id=str(result.owner_id),
            user_count=len(result.user_ids),
            created_at=result.created_at.isoformat(),
        ).model_dump()
    )


@projects_bp.route("/<project_id>", methods=["DELETE"])
@jwt_required()
@limiter.limit("5 per minute")
@require_permission("project:delete")
def delete_project(project_id: str):
    """Delete a project."""
    container = get_container()
    user_id = UUID(get_jwt_identity())

    try:
        project = container.get_project_usecase.execute(UUID(project_id))
    except ProjectNotFoundError:
        return (
            jsonify(
                ErrorResponse(error="NotFound", message=f"Project {project_id} not found", status_code=404).model_dump()
            ),
            404,
        )

    if not can_mutate_project(project, user_id):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    container.delete_project_usecase.execute(UUID(project_id))
    return "", 204


@projects_bp.route("/<project_id>/users", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def get_project_users(project_id: str):
    """Get users assigned to a project."""
    container = get_container()
    user_id = UUID(get_jwt_identity())

    try:
        project = container.get_project_usecase.execute(UUID(project_id))
    except ProjectNotFoundError:
        return (
            jsonify(
                ErrorResponse(error="NotFound", message=f"Project {project_id} not found", status_code=404).model_dump()
            ),
            404,
        )

    if not can_read_project(project, user_id):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    users = container.project_repository.get_project_users(UUID(project_id))

    return jsonify(
        ProjectUsersListResponse(
            users=[ProjectUserResponse(id=str(u[0]), email=u[1]) for u in users], total=len(users)
        ).model_dump()
    )


@projects_bp.route("/<project_id>/users", methods=["POST"])
@jwt_required()
def add_user_to_project(project_id: str):
    """DEPRECATED — invite-only signup is the only membership-creation path.

    This endpoint cannot satisfy the per-project role requirement (user_projects.role_id
    is NOT NULL after migration e3f1a2b4c5d6) and is replaced by:
      POST /api/v1/invitations  {project_id, email, role_id}

    Returns 410 Gone for any caller. Kept registered (rather than removed) so legacy
    clients receive a clear deprecation signal instead of a silent 404.
    """
    return (
        jsonify(
            ErrorResponse(
                error="Gone",
                message=(
                    "POST /projects/<id>/users is deprecated. Use "
                    "POST /api/v1/invitations with {project_id, email, role_id} instead."
                ),
                status_code=410,
            ).model_dump()
        ),
        410,
    )


@projects_bp.route("/<uuid:project_id>/members", methods=["GET"])
@jwt_required()
@limiter.limit("60 per minute")
def get_project_members(project_id: UUID):
    """Return project members with role and join date. Requires project membership."""
    from sqlalchemy import text
    container = get_container()
    user_id = UUID(get_jwt_identity())

    try:
        project = container.get_project_usecase.execute(project_id)
    except ProjectNotFoundError:
        return (
            jsonify(ErrorResponse(error="NotFound", message=f"Project {project_id} not found", status_code=404).model_dump()),
            404,
        )

    # Allow project owner or any member
    if project.owner_id != user_id and user_id not in project.user_ids:
        from flask_jwt_extended import get_jwt
        claims = get_jwt()
        if "*:*" not in set(claims.get("permissions", [])):
            return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    # Query members with role info via raw SQL (user_projects + roles + users join)
    from app import db
    rows = db.session.execute(
        text(
            """
            SELECT u.id, u.email, u.display_name, r.name AS role_name, up.assigned_at
            FROM user_projects up
            JOIN users u ON u.id = up.user_id
            LEFT JOIN roles r ON r.id = up.role_id
            WHERE up.project_id = :pid
            ORDER BY up.assigned_at
            """
        ),
        {"pid": str(project_id)},
    ).fetchall()

    members = [
        {
            "user_id": str(row[0]),
            "email": row[1],
            "display_name": row[2],
            "role_name": row[3],
            "joined_at": row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]
    return jsonify({"members": members, "total": len(members)}), 200


@projects_bp.route("/<project_id>/users/<user_id>", methods=["DELETE"])
@jwt_required()
@require_permission("project:manage_users")
def remove_user_from_project(project_id: str, user_id: str):
    """Remove a user from a project."""
    container = get_container()
    caller_id = UUID(get_jwt_identity())

    try:
        project = container.get_project_usecase.execute(UUID(project_id))
    except ProjectNotFoundError:
        return (
            jsonify(
                ErrorResponse(error="NotFound", message=f"Project {project_id} not found", status_code=404).model_dump()
            ),
            404,
        )

    if not can_mutate_project(project, caller_id):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    container.project_repository.remove_user(UUID(project_id), UUID(user_id))
    return "", 204
