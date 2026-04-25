"""RBAC decorators for project routes."""

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt

from app.api.v1.projects.schemas import ErrorResponse


def _has_permission(permissions: list, required: str) -> bool:
    """Check if permissions list includes required permission (with wildcard support)."""
    if required in permissions:
        return True
    if "*:*" in permissions:
        return True
    # Check resource wildcard (e.g., "project:*" matches "project:read")
    resource = required.split(":")[0]
    if f"{resource}:*" in permissions:
        return True
    return False


def require_permission(permission: str):
    """
    Decorator to check if current user has required permission.

    Usage:
        @require_permission("project:create")
        def create_project():
            ...
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            jwt_claims = get_jwt()
            permissions = jwt_claims.get("permissions", [])

            if not _has_permission(permissions, permission):
                return (
                    jsonify(
                        ErrorResponse(
                            error="Forbidden", message=f"Missing permission: {permission}", status_code=403
                        ).model_dump()
                    ),
                    403,
                )

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def has_permission(permission: str) -> bool:
    """Check if current user has a specific permission."""
    jwt_claims = get_jwt()
    permissions = jwt_claims.get("permissions", [])
    return _has_permission(permissions, permission)


def can_read_project(project, user_id: UUID) -> bool:
    """Admin (project:create), owner, or project member may read a project."""
    if has_permission("project:create"):
        return True
    return project.owner_id == user_id or user_id in project.user_ids


def can_mutate_project(project, user_id: UUID) -> bool:
    """Only admin (project:create) or project owner may modify/delete a project."""
    if has_permission("project:create"):
        return True
    return project.owner_id == user_id
