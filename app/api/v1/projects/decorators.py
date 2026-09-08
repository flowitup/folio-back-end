"""Permission decorators for project routes — the resolver is the only authority.

Every check here resolves `company role (+ project assignment) → matrix → D8
grants/denies` through `app.domain.authz.resolver`, memoized per request by
`app.api.v1.authz_context`. Nothing reads the token's `permissions` claim and
there is no owner bypass any more (D6): a project creator is an ordinary
assignee, backfilled by the platform-ops migration.
"""

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt_identity

from app.api.v1.ops_context import is_platform_ops
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


def _is_platform_admin() -> bool:
    """True when the caller holds the platform-ops flag (`users.is_platform_ops`).

    Read from the database on every request (never from the token), so granting
    or revoking ops applies immediately.
    """
    return is_platform_ops()


def _resolver_permissions_for_project(user_id: UUID, project_id: UUID) -> frozenset:
    """Company-matrix permissions (admin/manager/member) for a specific project.

    Delegates to the request-memoized resolver (app.api.v1.authz_context) so
    multiple decorator/serializer calls in the same request share one lookup.
    Local import avoids a hard dependency at module load (mirrors the existing
    `from wiring import get_container` pattern in this file).
    """
    from app.api.v1.authz_context import resolve_for_request

    return resolve_for_request(user_id, project_id=project_id, is_platform_admin=_is_platform_admin())


def _resolver_permissions_no_project(user_id: UUID) -> frozenset:
    """Resolver output for a route that resolves no project (e.g. `POST /projects`)."""
    from app.api.v1.authz_context import resolve_for_request

    return resolve_for_request(user_id, is_platform_admin=_is_platform_admin())


def _effective_permissions(kwargs: dict) -> list:
    """The caller's effective permissions for this request — resolver only.

    Resolves the request's target project (directly, or through the invoice /
    task / attachment in the URL) and returns the company matrix set for it,
    with D8 grants added and denies removed. A route that resolves no project
    gets the resolver's context-free answer (`project:create` for an admin of
    at least one company, plus `user:read`).

    Legacy global-role and membership-role permissions are NOT consulted: a
    stale token can no longer widen access, and a role change applies on the
    next request.
    """
    identity = get_jwt_identity()
    try:
        user_id = UUID(str(identity))
    except (ValueError, TypeError):
        return []
    project_id = _resolve_project_id(kwargs)
    if project_id is None:
        return list(_resolver_permissions_no_project(user_id))
    return list(_resolver_permissions_for_project(user_id, project_id))


def require_permission(permission: str):
    """
    Decorator to check if current user has required permission.

    Project-scoped routes are answered by the resolver for that project
    (company role + assignment + D8 rows); non-project routes get the
    resolver's context-free answer.

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


def _effective_perms_for(project_id: UUID, user_id: UUID) -> list:
    """The resolver's permission set for one project (see `_effective_permissions`)."""
    return list(_resolver_permissions_for_project(user_id, project_id))


def can_read_project(project, user_id: UUID) -> bool:
    """Return True when the resolver grants `project:read` on this project.

    Company admins read every project of their company; an assigned
    manager/member reads the ones they are assigned to. Neither the owner
    column nor bare `user_projects` membership is a bypass any more (D6) —
    reading requires a company role, which the platform-ops migration
    backfills for every existing owner.
    """
    return _has_permission(list(_resolver_permissions_for_project(user_id, project.id)), "project:read")


def can_mutate_project(project, user_id: UUID, permission: str = "project:update") -> bool:
    """Return True when the resolver grants `permission` on this project.

    The write gate is resource-aware: invoice/attachment/chiffrage routes pass
    `project:manage_invoices`, member management passes `project:manage_users`,
    project deletion passes `project:delete` (admin-only, D2) and everything
    else keeps the default `project:update`. Chaining the SAME permission the
    route's `require_permission(...)` already demands means a D8 grant actually
    unlocks the resource it names — and nothing more.
    """
    return _has_permission(list(_resolver_permissions_for_project(user_id, project.id)), permission)


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


def require_project_access(write: bool = False, permission: str = "project:update"):
    """Decorator: load the project from `<project_id>` → check the caller may read/write it.

    Args:
        write: gate on `permission` instead of `project:read`.
        permission: the write permission this resource needs — `project:manage_invoices`
            for invoice/chiffrage routes, `project:manage_users` for membership,
            `project:delete` for deletion, `project:update` (default) otherwise.
    """

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
            allowed = can_mutate_project(project, user_id, permission) if write else can_read_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _get_project_company_id_from_orm(project_id: UUID) -> "UUID | None":
    """Fetch company_id directly from the ORM model.

    The domain Project entity intentionally omits company_id (kept in the
    infrastructure layer). This helper queries the DB row so the decorator
    can resolve company ownership without extending the domain entity.
    """
    from app import db
    from app.infrastructure.database.models.project import ProjectModel

    row = db.session.get(ProjectModel, project_id)
    return row.company_id if row is not None else None


def _is_company_admin_for_project(project, user_id: UUID) -> bool:
    """Return True when the user is a company admin of the project's owning company.

    This allows company admins to read invoice attachments for their company's projects
    even when they are not a project member. Write paths are NOT widened by this check.
    """
    company_id = _get_project_company_id_from_orm(project.id)
    if company_id is None:
        return False
    from wiring import get_container  # local to avoid circular imports at module load

    container = get_container()
    access_repo = getattr(container, "user_company_access_repo", None)
    if access_repo is None:
        return False
    from app.application.billing.ports import admin_company_ids  # lazy to avoid circular imports

    return company_id in admin_company_ids(access_repo, user_id)


def require_invoice_access(
    write: bool = False,
    allow_company_admin: bool = False,
    permission: str = "project:manage_invoices",
):
    """Decorator: load invoice → load project → check caller is a member (or admin).

    Apply to any route whose path includes `<invoice_id>`. The decorator runs AFTER
    `@jwt_required()`. If the caller lacks access (not owner, not member, not admin),
    returns 403; if the invoice/project does not exist, returns 404.

    Args:
        write: when True, requires `permission` on the invoice's project;
               when False, `can_read_project`.
        permission: the write permission required — `project:manage_invoices`
               by default, since that is what an invoice write is.
        allow_company_admin: when True and write=False, also grants access to users
               who are admins of the project's owning company (even without membership).
               Has no effect on write paths.
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
            allowed = can_mutate_project(project, user_id, permission) if write else can_read_project(project, user_id)
            if not allowed and not write and allow_company_admin:
                allowed = _is_company_admin_for_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_task_access(write: bool = False, permission: str = "project:update"):
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
            allowed = can_mutate_project(project, user_id, permission) if write else can_read_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_attachment_access(
    write: bool = False,
    allow_company_admin: bool = False,
    permission: str = "project:manage_invoices",
):
    """Decorator: load attachment → invoice → project → check membership.

    Apply to routes whose path includes `<attachment_id>`. Same semantics as
    `require_invoice_access`.

    Args:
        write: when True, requires `permission` on the attachment's project;
               when False, `can_read_project`.
        permission: the write permission required — `project:manage_invoices` by default.
        allow_company_admin: when True and write=False, also grants access to users
               who are admins of the project's owning company (even without membership).
               Has no effect on write paths.
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
            allowed = can_mutate_project(project, user_id, permission) if write else can_read_project(project, user_id)
            if not allowed and not write and allow_company_admin:
                allowed = _is_company_admin_for_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator
