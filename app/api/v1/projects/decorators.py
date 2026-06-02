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


def _resolve_project_id(kwargs: dict) -> "UUID | None":
    """Resolve the project this request targets from the route's URL params.

    Project-scoped routes carry the project either directly (`project_id`) or
    via a child entity (`invoice_id`/`task_id`/`attachment_id`) that belongs to
    a project. Returns the owning project UUID, or None when no project context
    can be resolved (non-project routes, or the entity does not exist — in which
    case the downstream access decorator returns the proper 404).
    """
    from wiring import get_container

    raw = kwargs.get("project_id")
    if raw:
        try:
            return UUID(raw)
        except (ValueError, TypeError):
            return None

    container = get_container()
    invoice_repo = getattr(container, "invoice_repository", None)
    task_repo = getattr(container, "task_repository", None)
    attachment_repo = getattr(container, "invoice_attachment_repository", None)
    try:
        if kwargs.get("invoice_id") and invoice_repo is not None:
            invoice = invoice_repo.find_by_id(UUID(kwargs["invoice_id"]))
            return invoice.project_id if invoice else None
        if kwargs.get("task_id") and task_repo is not None:
            task = task_repo.find_by_id(UUID(kwargs["task_id"]))
            return task.project_id if task else None
        if kwargs.get("attachment_id") and attachment_repo is not None and invoice_repo is not None:
            attachment = attachment_repo.find_by_id(UUID(kwargs["attachment_id"]))
            if attachment is None:
                return None
            invoice = invoice_repo.find_by_id(attachment.invoice_id)
            return invoice.project_id if invoice else None
    except (ValueError, TypeError):
        return None
    return None


def _membership_role_permissions(user_id: UUID, project_id: UUID) -> set:
    """Permissions granted by the caller's membership role on a specific project.

    Project-membership roles (the role picked when inviting/adding a user to a
    project) grant capability scoped to that project only. Returns an empty set
    when the user is not a member or the role has no permissions.
    """
    from wiring import get_container

    container = get_container()
    membership_repo = getattr(container, "project_membership_repo", None)
    role_repo = getattr(container, "role_repository", None)
    if membership_repo is None or role_repo is None:
        return set()
    role_id = membership_repo.find_role_id(user_id, project_id)
    if role_id is None:
        return set()
    role = role_repo.find_by_id(role_id)
    if role is None:
        return set()
    return {perm.name for perm in role.permissions}


def _effective_permissions(kwargs: dict) -> list:
    """Caller's effective permissions for this request.

    Global-role permissions (from the JWT) UNION the permissions granted by the
    caller's membership role on the request's target project. The union is
    monotonic (only adds) so global-role behavior is never reduced; it lets a
    user invited as a project admin/manager exercise that project's capabilities
    even though their global role is the read-only default.
    """
    permissions = set(get_jwt().get("permissions", []))
    project_id = _resolve_project_id(kwargs)
    if project_id is not None:
        identity = get_jwt_identity()
        try:
            user_id = UUID(identity)
        except (ValueError, TypeError):
            return list(permissions)
        permissions |= _membership_role_permissions(user_id, project_id)
    return list(permissions)


def require_permission(permission: str):
    """
    Decorator to check if current user has required permission.

    For project-scoped routes the check uses the caller's *effective*
    permissions: global-role permissions UNION the permissions of their
    membership role on the target project. Non-project routes resolve no
    project and fall back to global-role permissions only.

    Usage:
        @require_permission("project:create")
        def create_project():
            ...
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            permissions = _effective_permissions(kwargs)

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


def _effective_perms_for(project_id: UUID, user_id: UUID) -> list:
    """Global-role permissions UNION the caller's membership-role permissions on a project."""
    permissions = set(get_jwt().get("permissions", []))
    permissions |= _membership_role_permissions(user_id, project_id)
    return list(permissions)


def can_read_project(project, user_id: UUID) -> bool:
    """Owner, project member, or any admin (effective project:create) may read a project."""
    if project.owner_id == user_id or user_id in project.user_ids:
        return True
    return _has_permission(_effective_perms_for(project.id, user_id), "project:create")


def can_mutate_project(project, user_id: UUID) -> bool:
    """Project owner or an admin (effective project:create — global OR per-project role) may modify a project.

    "Effective" means the caller's global-role permissions unioned with their
    membership-role permissions on this specific project, so a user invited as a
    project admin/manager can write within that project (per-project scope) even
    when their global role is the read-only default.
    """
    if project.owner_id == user_id:
        return True
    return _has_permission(_effective_perms_for(project.id, user_id), "project:create")


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
