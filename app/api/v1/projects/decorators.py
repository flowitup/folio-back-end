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


def _as_uuid(raw) -> "UUID | None":
    """Parse a URL parameter into a UUID.

    Routes declare their ids either as plain strings or through Flask's
    `<uuid:...>` converter, which hands the view a `UUID` object — `str(raw)`
    accepts both. Returns None for anything unparseable.
    """
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def _project_exists(project_id: UUID) -> bool:
    """True when a `projects` row with this id exists (unknown → assume it does).

    Answers the "does this id exist at all?" half of the 404-before-403 rule.
    Falls back to True when no authz reader is wired (minimal test doubles), so
    a wiring gap degrades to the permission check instead of a spurious 404.
    """
    from wiring import get_container

    reader = getattr(get_container(), "authz_reader", None)
    exists = getattr(reader, "project_exists", None)
    if exists is None:
        return True
    return bool(exists(project_id))


def _resolve_project_ref(kwargs: dict) -> "tuple[UUID | None, bool]":
    """Resolve the project this request targets → `(project_id, ref_is_missing)`.

    Project-scoped routes carry the project either directly (`project_id`) or
    via a child entity (`invoice_id`/`task_id`/`attachment_id`) that belongs to
    a project.

    `ref_is_missing` is True when the URL names a project (or an invoice / task
    / attachment) that does not exist: the caller gets 404 before any
    permission is evaluated, which is the pre-existing contract on these routes
    and matches the roster rule. An id that is present but unparseable, or a
    route carrying no project reference at all, yields `(None, False)` — the
    permission check then answers as usual.
    """
    from wiring import get_container

    raw = kwargs.get("project_id")
    if raw:
        project_id = _as_uuid(raw)
        if project_id is None:
            return None, False
        if not _project_exists(project_id):
            return None, True
        return project_id, False

    container = get_container()
    invoice_repo = getattr(container, "invoice_repository", None)
    task_repo = getattr(container, "task_repository", None)
    attachment_repo = getattr(container, "invoice_attachment_repository", None)

    if kwargs.get("invoice_id") and invoice_repo is not None:
        invoice_id = _as_uuid(kwargs["invoice_id"])
        if invoice_id is None:
            return None, False
        invoice = invoice_repo.find_by_id(invoice_id)
        return (invoice.project_id, False) if invoice else (None, True)
    if kwargs.get("task_id") and task_repo is not None:
        task_id = _as_uuid(kwargs["task_id"])
        if task_id is None:
            return None, False
        task = task_repo.find_by_id(task_id)
        return (task.project_id, False) if task else (None, True)
    if kwargs.get("attachment_id") and attachment_repo is not None and invoice_repo is not None:
        attachment_id = _as_uuid(kwargs["attachment_id"])
        if attachment_id is None:
            return None, False
        attachment = attachment_repo.find_by_id(attachment_id)
        if attachment is None:
            return None, True
        invoice = invoice_repo.find_by_id(attachment.invoice_id)
        return (invoice.project_id, False) if invoice else (None, True)
    return None, False


def _resolve_project_id(kwargs: dict) -> "UUID | None":
    """The project this request targets, or None (see `_resolve_project_ref`)."""
    project_id, _missing = _resolve_project_ref(kwargs)
    return project_id


def _missing_ref_message(kwargs: dict) -> str:
    """404 body for the URL id that could not be resolved to an existing row."""
    for key, label in (
        ("project_id", "Project"),
        ("invoice_id", "Invoice"),
        ("task_id", "Task"),
        ("attachment_id", "Attachment"),
    ):
        if kwargs.get(key):
            return f"{label} {kwargs[key]} not found"
    return "Not found"


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


def _permissions_for(project_id: "UUID | None") -> list:
    """The caller's effective permissions for `project_id` (context-free when None)."""
    identity = get_jwt_identity()
    user_id = _as_uuid(identity)
    if user_id is None:
        return []
    if project_id is None:
        return list(_resolver_permissions_no_project(user_id))
    return list(_resolver_permissions_for_project(user_id, project_id))


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
    return _permissions_for(_resolve_project_id(kwargs))


def require_permission(permission: str):
    """
    Decorator to check if current user has required permission.

    Project-scoped routes are answered by the resolver for that project
    (company role + assignment + D8 rows); non-project routes get the
    resolver's context-free answer. A URL naming a project (or invoice / task /
    attachment) that does not exist answers 404 before any permission is
    evaluated — an id nobody can act on is "not found", not "forbidden".

    Usage:
        @require_permission("project:create")
        def create_project():
            ...
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            project_id, ref_is_missing = _resolve_project_ref(kwargs)
            if ref_is_missing:
                return _not_found(_missing_ref_message(kwargs))
            permissions = _permissions_for(project_id)

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
            project_uuid = _as_uuid(project_id_str)
            if project_uuid is None:
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


def _require_entity_access(
    id_kwarg: str,
    label: str,
    project_for,
    write: bool,
    permission: str,
):
    """Build a decorator gating `<id_kwarg>` on the resolver, for one resource kind.

    `project_for(container, entity_id)` returns `(project, not_found_message)`:
    the project owning the URL entity, or None plus the 404 body to answer.
    Shared by the invoice / task / attachment decorators so all three answer
    404-before-403 and resolve permissions identically.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from wiring import get_container  # local import: avoids a circular dep at module load

            raw = kwargs.get(id_kwarg)
            if not raw:
                return _forbidden(f"Missing {label.lower()} id")
            entity_id = _as_uuid(raw)
            if entity_id is None:
                return _forbidden(f"Invalid {label.lower()} id")

            project, missing = project_for(get_container(), entity_id)
            if project is None:
                return _not_found(missing or f"{label} {raw} not found")

            user_id = UUID(str(get_jwt_identity()))
            allowed = can_mutate_project(project, user_id, permission) if write else can_read_project(project, user_id)
            if not allowed:
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _project_of_invoice(container, invoice_id: UUID):
    invoice = container.invoice_repository.find_by_id(invoice_id)
    if invoice is None:
        return None, f"Invoice {invoice_id} not found"
    project = container.project_repository.find_by_id(invoice.project_id)
    return project, "Invoice's project no longer exists"


def _project_of_task(container, task_id: UUID):
    task = container.task_repository.find_by_id(task_id)
    if task is None:
        return None, f"Task {task_id} not found"
    project = container.project_repository.find_by_id(task.project_id)
    return project, "Task's project no longer exists"


def _project_of_attachment(container, attachment_id: UUID):
    attachment = container.invoice_attachment_repository.find_by_id(attachment_id)
    if attachment is None:
        return None, f"Attachment {attachment_id} not found"
    invoice = container.invoice_repository.find_by_id(attachment.invoice_id)
    if invoice is None:
        return None, "Attachment's invoice no longer exists"
    project = container.project_repository.find_by_id(invoice.project_id)
    return project, "Attachment's project no longer exists"


def require_invoice_access(write: bool = False, permission: str = "project:manage_invoices"):
    """Decorator: load invoice → its project → resolve the caller's permission.

    Apply to any route whose path includes `<invoice_id>`; runs after
    `@jwt_required()`. 404 when the invoice (or its project) is gone, 403 when
    the caller lacks `project:read` (reads) or `permission` (writes). Company
    admins pass through the resolver like any other caller — their read of a
    company project needs no special case.
    """
    return _require_entity_access("invoice_id", "Invoice", _project_of_invoice, write, permission)


def require_task_access(write: bool = False, permission: str = "project:update"):
    """Decorator: load task → its project → resolve the caller's permission.

    Apply to routes whose path includes `<task_id>`; runs after `@jwt_required()`.
    """
    return _require_entity_access("task_id", "Task", _project_of_task, write, permission)


def require_attachment_access(write: bool = False, permission: str = "project:manage_invoices"):
    """Decorator: load attachment → invoice → project → resolve the caller's permission.

    Apply to routes whose path includes `<attachment_id>`. Same semantics as
    `require_invoice_access`.
    """
    return _require_entity_access("attachment_id", "Attachment", _project_of_attachment, write, permission)
