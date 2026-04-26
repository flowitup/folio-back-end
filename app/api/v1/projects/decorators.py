"""RBAC decorators for project routes."""

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity

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


# ---------------------------------------------------------------------------
# Per-resource authorization helpers (defends against IDOR by UUID guessing)
# ---------------------------------------------------------------------------


def _forbidden(message: str = "Access denied"):
    return (
        jsonify(ErrorResponse(error="Forbidden", message=message, status_code=403).model_dump()),
        403,
    )


def _not_found(message: str):
    return (
        jsonify(ErrorResponse(error="NotFound", message=message, status_code=404).model_dump()),
        404,
    )


def require_project_access(write: bool = False):
    """Decorator: load project from `<project_id>` URL param → check membership."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from wiring import get_container

            project_id_str = kwargs.get("project_id")
            if not project_id_str:
                return _forbidden("Missing project id")
            try:
                project_uuid = UUID(project_id_str)
            except ValueError:
                return _forbidden("Invalid project id")

            container = get_container()
            project = container.project_repository.find_by_id(project_uuid)
            if project is None:
                return _not_found(f"Project {project_id_str} not found")

            user_id = UUID(get_jwt_identity())
            allowed = can_mutate_project(project, user_id) if write else can_read_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_invoice_access(write: bool = False):
    """Decorator: load invoice → load project → check caller is a member (or admin).

    Apply to any route whose path includes `<invoice_id>`. The decorator runs AFTER
    `@jwt_required()`. If the caller lacks access (not owner, not member, not admin),
    returns 403; if the invoice/project does not exist, returns 404.

    Args:
        write: when True, requires `can_mutate_project` (owner or admin only);
               when False, `can_read_project` (any project member).
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from wiring import get_container  # local import to avoid circular dep at module load

            invoice_id_str = kwargs.get("invoice_id")
            if not invoice_id_str:
                return _forbidden("Missing invoice id")
            try:
                invoice_uuid = UUID(invoice_id_str)
            except ValueError:
                return _forbidden("Invalid invoice id")

            container = get_container()
            invoice = container.invoice_repository.find_by_id(invoice_uuid)
            if invoice is None:
                return _not_found(f"Invoice {invoice_id_str} not found")
            project = container.project_repository.find_by_id(invoice.project_id)
            if project is None:
                return _not_found("Invoice's project no longer exists")

            user_id = UUID(get_jwt_identity())
            allowed = can_mutate_project(project, user_id) if write else can_read_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_task_access(write: bool = False):
    """Decorator: load task → invoice's project → check membership.

    Apply to routes whose path includes `<task_id>`. JWT is required
    upstream via `@jwt_required()`.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from wiring import get_container

            task_id_str = kwargs.get("task_id")
            if not task_id_str:
                return _forbidden("Missing task id")
            try:
                task_uuid = UUID(task_id_str)
            except ValueError:
                return _forbidden("Invalid task id")

            container = get_container()
            task = container.task_repository.find_by_id(task_uuid)
            if task is None:
                return _not_found(f"Task {task_id_str} not found")
            project = container.project_repository.find_by_id(task.project_id)
            if project is None:
                return _not_found("Task's project no longer exists")

            user_id = UUID(get_jwt_identity())
            allowed = can_mutate_project(project, user_id) if write else can_read_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_attachment_access(write: bool = False):
    """Decorator: load attachment → invoice → project → check membership.

    Apply to routes whose path includes `<attachment_id>`. Same semantics as
    `require_invoice_access`.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from wiring import get_container

            att_id_str = kwargs.get("attachment_id")
            if not att_id_str:
                return _forbidden("Missing attachment id")
            try:
                att_uuid = UUID(att_id_str)
            except ValueError:
                return _forbidden("Invalid attachment id")

            container = get_container()
            attachment = container.invoice_attachment_repository.find_by_id(att_uuid)
            if attachment is None:
                return _not_found(f"Attachment {att_id_str} not found")
            invoice = container.invoice_repository.find_by_id(attachment.invoice_id)
            if invoice is None:
                return _not_found("Attachment's invoice no longer exists")
            project = container.project_repository.find_by_id(invoice.project_id)
            if project is None:
                return _not_found("Attachment's project no longer exists")

            user_id = UUID(get_jwt_identity())
            allowed = can_mutate_project(project, user_id) if write else can_read_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator
