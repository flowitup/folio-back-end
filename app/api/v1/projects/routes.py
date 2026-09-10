"""Project API routes."""

from decimal import Decimal
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from pydantic import ValidationError

from app.api.openapi import openapi_doc
from app.api.v1.projects import projects_bp
from app.api.v1.projects.schemas import (
    CreateProjectRequest,
    UpdateProjectRequest,
    ProjectResponse,
    ProjectListResponse,
    ErrorResponse,
    ProjectUserResponse,
    ProjectUsersListResponse,
)
from app.api.v1.projects.decorators import (
    require_permission,
    can_read_project,
    can_mutate_project,
    _effective_perms_for,
    _has_permission,
    _is_platform_admin,
)
from app.application.billing.ports import admin_company_ids
from app.application.projects import CreateProjectRequest as CreateDTO
from app.application.projects.ports import ProjectSpent
from app.domain.authz.resolver import effective_permissions as _authz_effective_permissions
from app.domain.entities.project_membership import ProjectMembership
from app.domain.exceptions.project_exceptions import ProjectNotFoundError, InvalidProjectDataError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

# Zero rollup for projects with no spend, or when the reader is not wired.
_NO_SPEND = ProjectSpent(
    total=Decimal("0"),
    by_credits=Decimal("0"),
    personal=Decimal("0"),
    labor_accrued=Decimal("0"),
    labor_paid=Decimal("0"),
    labor_unpaid=Decimal("0"),
    personal_by_type={},
)


def _spent_for(spent_map: dict, project_id: UUID) -> ProjectSpent:
    """Look up a project's spend rollup, defaulting to zero on every figure."""
    return spent_map.get(project_id, _NO_SPEND)


def _spend_visible(perms: list) -> bool:
    """Spend rollups need `project:manage_labor` or `project:view_pay`.

    Project ownership grants nothing on its own (D6): an owner sees money
    exactly like any other manager, through the resolver.
    """
    return _has_permission(perms, "project:manage_labor") or _has_permission(perms, "project:view_pay")


def _budget_visible(perms: list) -> bool:
    """The budget is the financing side, gated on its own `project:view_budget`.

    Split from `_spend_visible` on purpose: a manager runs what the project
    spends but not what funds it, so they read every spend figure and none of
    the budget. Admins hold the permission company-wide; anyone else needs a D8
    grant.
    """
    return _has_permission(perms, "project:view_budget")


def _spend_fields(rollup: ProjectSpent) -> dict:
    """Serialize a spend rollup into the ProjectResponse money fields."""
    return {
        "spent": float(rollup.total),
        "spent_by_credits": float(rollup.by_credits),
        "spent_personal": float(rollup.personal),
        "labor_accrued": float(rollup.labor_accrued),
        "labor_paid": float(rollup.labor_paid),
        "labor_unpaid": float(rollup.labor_unpaid),
        "personal_by_type": {k: float(v) for k, v in rollup.personal_by_type.items()},
    }


@projects_bp.route("", methods=["GET"])
@openapi_doc(summary="List projects for current user", tags=["projects"])
@jwt_required()
def list_projects():
    """List projects visible to current user.

    Visible = every project of a company the caller administers ∪ the projects
    they are assigned to ∪ everything for platform ops. The list is its own
    authorization: it is scoped per caller, so it carries no
    `require_permission` gate (there is no project to resolve one against) and
    answers an empty list to a caller who can see nothing.
    """
    container = get_container()
    user_id = get_jwt_identity()
    is_platform_admin = _is_platform_admin()
    admin_ids = admin_company_ids(container.user_company_access_repo, UUID(user_id)) if not is_platform_admin else []

    projects = container.list_projects_usecase.execute(
        UUID(user_id), admin_company_ids=admin_ids, is_platform_admin=is_platform_admin
    )

    # Batch-load budget+source from project entities (carried via repo→entity)
    # and spent via a single aggregation call (no N+1).
    from app.infrastructure.database.models.project import ProjectModel
    from app import db

    project_ids = [UUID(str(p.id)) for p in projects]

    # Prime the per-request authz-reader cache with everything the resolver
    # needs for the whole list — project→company, the caller's assignments and
    # their D8 rows — in three queries instead of three per project. No-op if
    # authz_reader isn't wired or doesn't support preloading (a minimal test
    # double).
    if project_ids and container.authz_reader is not None:
        preload = getattr(container.authz_reader, "preload_for_projects", None)
        if preload is not None:
            preload(UUID(user_id), project_ids)

    spent_map = {}
    if project_ids and container.project_spent_reader is not None:
        spent_map = container.project_spent_reader.sum_spent_by_projects(project_ids)

    # Fetch budget + company_id fields from the DB models (not exposed via ProjectSummary DTO).
    budget_map: dict = {}
    company_id_map: dict = {}
    if project_ids:
        rows = (
            db.session.query(
                ProjectModel.id,
                ProjectModel.budget,
                ProjectModel.budget_source,
                ProjectModel.company_id,
            )
            .filter(ProjectModel.id.in_(project_ids))
            .all()
        )
        for row in rows:
            budget_map[row.id] = (row.budget, row.budget_source)
            company_id_map[row.id] = str(row.company_id) if row.company_id else None

    user_uuid = UUID(user_id)
    items = []
    for p in projects:
        pid = UUID(str(p.id))
        perms = sorted(_effective_perms_for(pid, user_uuid))
        # A row the caller cannot open is not part of their list: ownership and
        # a bare assignment row no longer imply a company role, so the query's
        # visibility clauses can still surface a project the resolver refuses
        # (403 on every one of its routes). Filtering here also keeps `total`
        # honest.
        if not _has_permission(perms, "project:read"):
            continue
        spend_visible = _spend_visible(perms)
        budget_visible = _budget_visible(perms)
        items.append(
            ProjectResponse(
                id=p.id,
                name=p.name,
                address=p.address,
                owner_id=p.owner_id,
                user_count=p.user_count,
                created_at="",
                company_id=company_id_map.get(pid),
                my_permissions=perms,
                budget=(
                    float(budget_map[pid][0])
                    if budget_visible and budget_map.get(pid, (None,))[0] is not None
                    else None
                ),
                budget_source=budget_map.get(pid, (None, None))[1] if budget_visible else None,
                **_spend_fields(_spent_for(spent_map, pid) if spend_visible else _NO_SPEND),
            )
        )
    return jsonify(ProjectListResponse(projects=items, total=len(items)).model_dump())


@projects_bp.route("", methods=["POST"])
@openapi_doc(
    summary="Create a new project",
    request=CreateProjectRequest,
    responses={201: ProjectResponse},
    tags=["projects"],
)
@jwt_required()
@limiter.limit("10 per minute")
def create_project():
    """Create a new project.

    `project:create` is meaningful only *per company*. Company resolution:
      1. body `company_id`, if given — must be a company the caller admins
         (403 otherwise; platform ops may name any existing company).
      2. else the caller's primary company, if they admin it.
      3. else the single company the caller admins.
      4. else 400 — every project belongs to a company (`projects.company_id`
         is NOT NULL), so there is no orphan path, not even for platform ops.
    `project:create` is then required from the resolver for the resolved
    company. The creator is assigned to the new project so that, with the
    owner bypass gone (D6), they keep working on it and see it in
    `GET /projects`.
    """
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
    user_id = UUID(get_jwt_identity())
    is_platform_admin = _is_platform_admin()
    access_repo = container.user_company_access_repo
    admin_ids = admin_company_ids(access_repo, user_id)

    target_company_id: "UUID | None" = None
    if data.company_id:
        try:
            body_company_id = UUID(data.company_id)
        except ValueError:
            return (
                jsonify(
                    ErrorResponse(error="ValidationError", message="Invalid company_id", status_code=400).model_dump()
                ),
                400,
            )
        if body_company_id not in admin_ids and not is_platform_admin:
            return (
                jsonify(
                    ErrorResponse(
                        error="Forbidden", message="Not an admin of that company", status_code=403
                    ).model_dump()
                ),
                403,
            )
        target_company_id = body_company_id
    else:
        if access_repo is not None:
            for access in access_repo.list_for_user(user_id):
                if access.is_primary and access.company_id in admin_ids:
                    target_company_id = access.company_id
                    break
        if target_company_id is None and len(admin_ids) == 1:
            target_company_id = admin_ids[0]
        if target_company_id is None:
            return (
                jsonify(
                    ErrorResponse(
                        error="ValidationError",
                        message="No company to attach this project to — pass company_id",
                        status_code=400,
                    ).model_dump()
                ),
                400,
            )

    # A non-admin's body_company_id is already rejected above (it can never be
    # in admin_ids for a company that doesn't exist), but a platform admin
    # skips that check entirely — validate existence here so a bogus id 400s
    # instead of hitting the projects.company_id FK constraint inside
    # create_project_usecase (a 500).
    if container.company_repo is not None and container.company_repo.find_by_id(target_company_id) is None:
        return (
            jsonify(
                ErrorResponse(
                    error="ValidationError", message="company_id does not exist", status_code=400
                ).model_dump()
            ),
            400,
        )
    reader = container.authz_reader
    perms = (
        _authz_effective_permissions(reader, user_id, company_id=target_company_id, is_platform_admin=is_platform_admin)
        if reader is not None
        else (frozenset({"*:*"}) if is_platform_admin else frozenset())
    )
    if not _has_permission(list(perms), "project:create"):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    try:
        result = container.create_project_usecase.execute(
            CreateDTO(
                name=data.name,
                address=data.address,
                owner_id=user_id,
                budget=data.budget,
                budget_source=data.budget_source,
                company_id=target_company_id,
            )
        )
    except InvalidProjectDataError as e:
        return jsonify(ErrorResponse(error="ValidationError", message=str(e), status_code=400).model_dump()), 400

    # Assign the creator to their own project: with the owner bypass gone (D6)
    # the assignment row is what lets a company manager keep working on it, and
    # what makes the project show up in their `GET /projects`.
    creator_assigned = False
    if container.project_membership_repo is not None:
        creator_assigned = container.project_membership_repo.add(
            ProjectMembership.create(user_id=user_id, project_id=UUID(result.id))
        )
        # ProjectMembership.add() only flushes (mirrors BulkAddExistingUserUseCase);
        # commit explicitly so the row survives past this request's teardown.
        from app import db as _db

        _db.session.commit()

    return (
        jsonify(
            ProjectResponse(
                id=result.id,
                name=result.name,
                address=result.address,
                owner_id=result.owner_id,
                user_count=1 if creator_assigned else 0,
                created_at=result.created_at,
                company_id=result.company_id,
                invoice_prefix=result.invoice_prefix,
                budget=float(result.budget) if result.budget is not None else None,
                budget_source=result.budget_source,
                spent=0,
            ).model_dump()
        ),
        201,
    )


@projects_bp.route("/<project_id>", methods=["GET"])
@openapi_doc(summary="Get a single project by ID", responses={200: ProjectResponse}, tags=["projects"])
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

    # Resolve company_id from the DB model (not exposed on the domain entity).
    from app import db
    from app.infrastructure.database.models.project import ProjectModel

    db_row = db.session.get(ProjectModel, project.id)
    company_id_str = str(db_row.company_id) if db_row and db_row.company_id else None

    # Compute spent for this single project.
    container = get_container()
    spent_rollup = _NO_SPEND
    if container.project_spent_reader is not None:
        spent_map = container.project_spent_reader.sum_spent_by_projects([project.id])
        spent_rollup = _spent_for(spent_map, project.id)

    perms = sorted(_effective_perms_for(project.id, user_id))
    spend_visible = _spend_visible(perms)
    budget_visible = _budget_visible(perms)
    return jsonify(
        ProjectResponse(
            id=str(project.id),
            name=project.name,
            address=project.address,
            owner_id=str(project.owner_id),
            user_count=len(project.user_ids),
            created_at=project.created_at.isoformat(),
            company_id=company_id_str,
            invoice_prefix=project.invoice_prefix,
            my_permissions=perms,
            budget=float(project.budget) if budget_visible and project.budget is not None else None,
            budget_source=project.budget_source if budget_visible else None,
            **_spend_fields(spent_rollup if spend_visible else _NO_SPEND),
        ).model_dump()
    )


@projects_bp.route("/<project_id>", methods=["PUT"])
@openapi_doc(
    summary="Update an existing project",
    request=UpdateProjectRequest,
    responses={200: ProjectResponse},
    tags=["projects"],
)
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

    perms = sorted(_effective_perms_for(UUID(project_id), user_id))
    budget_visible = _budget_visible(perms)
    spend_visible = _spend_visible(perms)
    # Writing a figure you are not allowed to read would let a manager overwrite
    # the owner's budget blind, so the write follows the read.
    if not budget_visible and ({"budget", "budget_source"} & data.model_fields_set):
        return (
            jsonify(
                ErrorResponse(
                    error="Forbidden",
                    message="Changing the budget requires project:view_budget",
                    status_code=403,
                ).model_dump()
            ),
            403,
        )

    try:
        result = container.update_project_usecase.execute(
            UUID(project_id),
            name=data.name,
            address=data.address,
            invoice_prefix=data.invoice_prefix,
            budget=data.budget,
            budget_source=data.budget_source,
            provided_fields=data.model_fields_set,
        )
    except InvalidProjectDataError as e:
        return jsonify(ErrorResponse(error="ValidationError", message=str(e), status_code=400).model_dump()), 400

    # Compute spent for the updated project.
    spent_rollup = _NO_SPEND
    if container.project_spent_reader is not None:
        spent_map = container.project_spent_reader.sum_spent_by_projects([result.id])
        spent_rollup = _spent_for(spent_map, result.id)

    return jsonify(
        ProjectResponse(
            id=str(result.id),
            name=result.name,
            address=result.address,
            owner_id=str(result.owner_id),
            user_count=len(result.user_ids),
            created_at=result.created_at.isoformat(),
            invoice_prefix=result.invoice_prefix,
            budget=float(result.budget) if budget_visible and result.budget is not None else None,
            budget_source=result.budget_source if budget_visible else None,
            **_spend_fields(spent_rollup if spend_visible else _NO_SPEND),
        ).model_dump()
    )


@projects_bp.route("/<project_id>", methods=["DELETE"])
@openapi_doc(summary="Delete a project", tags=["projects"])
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

    if not can_mutate_project(project, user_id, "project:delete"):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    container.delete_project_usecase.execute(UUID(project_id))
    return "", 204


@projects_bp.route("/<project_id>/users", methods=["GET"])
@openapi_doc(summary="Get users assigned to a project", tags=["projects"])
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
@openapi_doc(summary="DEPRECATED — invite-only signup is the only membership-creation path", tags=["projects"])
@jwt_required()
def add_user_to_project(project_id: str):
    """DEPRECATED — invite-only signup is the only membership-creation path.

    Replaced by ``POST /api/v1/invitations {project_id, email}`` for outsiders
    and ``PUT /projects/<id>/assignments/<user_id>`` for existing company
    members. Returns 410 Gone for any caller. Kept registered (rather than
    removed) so legacy clients receive a clear deprecation signal instead of a
    silent 404.
    """
    return (
        jsonify(
            ErrorResponse(
                error="Gone",
                message=(
                    "POST /projects/<id>/users is deprecated. Use "
                    "POST /api/v1/invitations with {project_id, email} instead."
                ),
                status_code=410,
            ).model_dump()
        ),
        410,
    )


@projects_bp.route("/<uuid:project_id>/members", methods=["GET"])
@openapi_doc(summary="Return the people assigned to a project with their company role", tags=["projects"])
@jwt_required()
@limiter.limit("60 per minute")
def get_project_members(project_id: UUID):
    """Return the people assigned to a project. Requires read access to the project.

    ``role_name`` is the member's **company** role (`admin` | `manager` |
    `member`) — an assignment itself carries no role. It is null when the
    project has no company or the person is no longer attached to it.
    """
    from sqlalchemy import text

    container = get_container()
    user_id = UUID(get_jwt_identity())

    try:
        project = container.get_project_usecase.execute(project_id)
    except ProjectNotFoundError:
        return (
            jsonify(
                ErrorResponse(error="NotFound", message=f"Project {project_id} not found", status_code=404).model_dump()
            ),
            404,
        )

    # Reading the member list needs read access to the project itself.
    if not can_read_project(project, user_id):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    from app import db

    reader = container.authz_reader
    company_id = reader.project_company_id(project_id) if reader is not None else None

    rows = db.session.execute(
        text(
            """
            SELECT u.id, u.email, u.display_name, uca.role AS role_name, up.assigned_at
            FROM user_projects up
            JOIN users u ON u.id = up.user_id
            LEFT JOIN user_company_access uca
                   ON uca.user_id = up.user_id AND uca.company_id = :cid
            WHERE up.project_id = :pid
            ORDER BY up.assigned_at
            """
        ),
        {"pid": str(project_id), "cid": str(company_id) if company_id else None},
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
@openapi_doc(summary="Remove a user from a project", tags=["projects"])
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

    if not can_mutate_project(project, caller_id, "project:manage_users"):
        return jsonify(ErrorResponse(error="Forbidden", message="Access denied", status_code=403).model_dump()), 403

    container.project_repository.remove_user(UUID(project_id), UUID(user_id))
    notifier = container.membership_push_notifier
    if notifier is not None:
        notifier.notify(
            "project_member_removed",
            user_id=UUID(user_id),
            actor_id=caller_id,
            entity_id=UUID(project_id),
        )
    return "", 204
