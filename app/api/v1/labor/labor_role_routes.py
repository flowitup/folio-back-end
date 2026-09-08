"""Labor role CRUD API routes."""

from __future__ import annotations

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api.openapi import openapi_doc
from app.api.v1.labor import labor_bp
from app.api.v1.labor._labor_validation_error_helper import (
    _error_response,
    validation_error_response as _validation_error_response,
)
from app.api.v1.labor.labor_role_schemas import (
    CreateLaborRoleRequest,
    LaborRoleListResponse,
    LaborRoleResponse,
    ROLE_COLOR_PALETTE,
    UpdateLaborRoleRequest,
)
from app.domain.exceptions.labor_exceptions import (
    DuplicateLaborRoleError,
    LaborRoleNotFoundError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _role_response(role) -> LaborRoleResponse:
    return LaborRoleResponse(
        id=str(role.id),
        name=role.name,
        color=role.color,
        created_at=role.created_at.isoformat(),
        slug=getattr(role, "slug", None),
    )


_MANAGE_ROLES = ("admin", "manager")


def _require_company_admin_or_manager(caller_id: UUID, company_id):
    """M10: creating/updating/deleting a labor role requires company admin or
    manager of `company_id` — the routes previously had no role check beyond
    `@jwt_required()`, letting any authenticated member (of ANY company)
    mutate roles.

    `company_id=None` (legacy/unscoped rows, no company context resolved) is
    restricted to a platform admin — there is no per-company role to check
    against, so anyone-but-a-platform-admin would otherwise get a blank check.
    Returns an error response, or None if the caller is authorized.
    """
    container = get_container()
    role_checker = getattr(container, "authorization_service", None)
    is_platform_admin = role_checker is not None and role_checker.is_platform_admin(caller_id)
    if is_platform_admin:
        return None

    if company_id is None:
        return _error_response("Forbidden", "Admin permission required", 403)

    access_repo = getattr(container, "user_company_access_repo", None)
    access = access_repo.find(caller_id, company_id) if access_repo is not None else None
    if access is None or access.role not in _MANAGE_ROLES:
        return _error_response("Forbidden", "Company admin or manager permission required", 403)
    return None


def _resolve_company_scope():
    """Resolve the company scope for the global /labor/roles routes (Phase 2).

    `?company_id=` is honored only when the caller belongs to that company
    (any role) — returns a (company_id, error_response) tuple where exactly
    one element is None. Falls back to the caller's primary company; when
    neither resolves (no query param and no primary company — e.g. a
    platform admin with no company of their own, or a test fixture with no
    company set up at all), scope stays None (legacy/unscoped rows) rather
    than failing, so callers with no company context keep working.
    """
    container = get_container()
    caller_id = UUID(get_jwt_identity())

    raw_company_id = request.args.get("company_id")
    if raw_company_id:
        try:
            requested = UUID(raw_company_id)
        except ValueError:
            return None, _error_response("ValidationError", f"Invalid company id: {raw_company_id!r}", 400)
        access_repo = getattr(container, "user_company_access_repo", None)
        access = access_repo.find(caller_id, requested) if access_repo is not None else None
        if access is None:
            return None, _error_response("Forbidden", f"Not a member of company {raw_company_id}", 403)
        return requested, None

    authz_reader = getattr(container, "authz_reader", None)
    if authz_reader is not None:
        primary = authz_reader.primary_company_id(caller_id)
        if primary is not None:
            return primary, None
    return None, None


@labor_bp.route("/labor/roles", methods=["GET"])
@openapi_doc(
    summary="List labor roles (scoped to the caller's company) with the suggested color palette", tags=["labor"]
)
@jwt_required()
def list_labor_roles():
    """List labor roles scoped to the caller's company, with the suggested color palette."""
    company_id, error = _resolve_company_scope()
    if error is not None:
        return error
    roles = get_container().list_labor_roles_usecase.execute(company_id=company_id)
    return jsonify(
        LaborRoleListResponse(
            roles=[_role_response(r) for r in roles],
            palette=ROLE_COLOR_PALETTE,
        ).model_dump()
    )


@labor_bp.route("/labor/roles", methods=["POST"])
@openapi_doc(
    summary="Create a new labor role, scoped to the caller's company",
    request=CreateLaborRoleRequest,
    responses={201: LaborRoleResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("10 per minute")
def create_labor_role():
    """Create a new labor role, scoped to the caller's company."""
    try:
        data = CreateLaborRoleRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    company_id, error = _resolve_company_scope()
    if error is not None:
        return error

    caller_id = UUID(get_jwt_identity())
    auth_error = _require_company_admin_or_manager(caller_id, company_id)
    if auth_error is not None:
        return auth_error

    try:
        role = get_container().create_labor_role_usecase.execute(
            name=data.name,
            color=data.color,
            company_id=company_id,
        )
    except DuplicateLaborRoleError as e:
        return _error_response("Conflict", str(e), 409)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(_role_response(role).model_dump()), 201


@labor_bp.route("/labor/roles/<role_id>", methods=["PATCH"])
@openapi_doc(
    summary="Update name and/or color of a labor role",
    request=UpdateLaborRoleRequest,
    responses={200: LaborRoleResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("10 per minute")
def update_labor_role(role_id: str):
    """Update name and/or color of a labor role (company admin or manager)."""
    try:
        data = UpdateLaborRoleRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    container = get_container()
    existing = container.labor_role_repository.find_by_id(UUID(role_id))
    if existing is None:
        return _error_response("NotFound", f"Labor role {role_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    auth_error = _require_company_admin_or_manager(caller_id, existing.company_id)
    if auth_error is not None:
        return auth_error

    try:
        role = container.update_labor_role_usecase.execute(
            role_id=UUID(role_id),
            name=data.name,
            color=data.color,
        )
    except LaborRoleNotFoundError:
        return _error_response("NotFound", f"Labor role {role_id} not found", 404)
    except DuplicateLaborRoleError as e:
        return _error_response("Conflict", str(e), 409)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(_role_response(role).model_dump())


@labor_bp.route("/labor/roles/<role_id>", methods=["DELETE"])
@openapi_doc(summary="Delete a labor role", tags=["labor"])
@jwt_required()
@limiter.limit("10 per minute")
def delete_labor_role(role_id: str):
    """Delete a labor role (company admin or manager). Workers referencing it
    will have their role cleared."""
    container = get_container()
    existing = container.labor_role_repository.find_by_id(UUID(role_id))
    if existing is None:
        return _error_response("NotFound", f"Labor role {role_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    auth_error = _require_company_admin_or_manager(caller_id, existing.company_id)
    if auth_error is not None:
        return auth_error

    try:
        container.delete_labor_role_usecase.execute(role_id=UUID(role_id))
    except LaborRoleNotFoundError:
        return _error_response("NotFound", f"Labor role {role_id} not found", 404)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return "", 204
