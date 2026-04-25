"""Auth middleware - decorators for permission/role-based access."""

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, get_jwt

from wiring import get_container


def require_permission(*required_permissions):
    """
    Decorator requiring specific permissions.

    Usage: @require_permission("project:create", "project:update")
    Requires ALL listed permissions.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            jwt_claims = get_jwt()
            user_permissions = set(jwt_claims.get("permissions", []))

            # Check for superadmin
            if "*:*" in user_permissions:
                return fn(*args, **kwargs)

            if not all(p in user_permissions for p in required_permissions):
                return (
                    jsonify(
                        {
                            "error": "Forbidden",
                            "message": f"Required permissions: {', '.join(required_permissions)}",
                            "status_code": 403,
                        }
                    ),
                    403,
                )

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_any_permission(*required_permissions):
    """
    Decorator requiring any of the specified permissions.

    Usage: @require_any_permission("project:read", "project:admin")
    Requires at least ONE listed permission.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            jwt_claims = get_jwt()
            user_permissions = set(jwt_claims.get("permissions", []))

            # Check for superadmin
            if "*:*" in user_permissions:
                return fn(*args, **kwargs)

            if not any(p in user_permissions for p in required_permissions):
                return (
                    jsonify(
                        {
                            "error": "Forbidden",
                            "message": f"Required one of: {', '.join(required_permissions)}",
                            "status_code": 403,
                        }
                    ),
                    403,
                )

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_role(*required_roles):
    """
    Decorator requiring specific roles.

    Usage: @require_role("admin")
    Requires at least ONE listed role.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            user_id = get_jwt_identity()

            container = get_container()
            authz = container.authorization_service

            if not any(authz.has_role(UUID(user_id), role) for role in required_roles):
                return (
                    jsonify(
                        {
                            "error": "Forbidden",
                            "message": f"Required role: {', '.join(required_roles)}",
                            "status_code": 403,
                        }
                    ),
                    403,
                )

            return fn(*args, **kwargs)

        return wrapper

    return decorator
