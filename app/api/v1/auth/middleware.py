"""Auth middleware - permission decorator for non-project routes.

Deprecated: project-scoped routes use `app.api.v1.projects.decorators`, which
resolves the target project. This decorator answers the context-free question
("does any of the caller's companies grant this?") and is removed once its last
consumer is gone.
"""

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app.api.v1.ops_context import is_platform_ops


def require_permission(*required_permissions):
    """
    Decorator requiring specific permissions.

    Usage: @require_permission("project:create", "project:update")
    Requires ALL listed permissions, resolved from the caller's company roles
    (never from the token). Platform ops bypasses the check.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            if is_platform_ops():
                return fn(*args, **kwargs)

            from wiring import get_container

            authz = getattr(get_container(), "authorization_service", None)
            user_id = UUID(str(get_jwt_identity()))
            granted = authz is not None and all(authz.has_permission(user_id, p) for p in required_permissions)
            if not granted:
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
