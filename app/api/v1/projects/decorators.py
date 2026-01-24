"""RBAC decorators for project routes."""

from functools import wraps
from flask import jsonify
from flask_jwt_extended import get_jwt

from app.api.v1.projects.schemas import ErrorResponse


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

            if permission not in permissions:
                return jsonify(ErrorResponse(
                    error="Forbidden",
                    message=f"Missing permission: {permission}",
                    status_code=403
                ).model_dump()), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorator


def has_permission(permission: str) -> bool:
    """Check if current user has a specific permission."""
    jwt_claims = get_jwt()
    permissions = jwt_claims.get("permissions", [])
    return permission in permissions
