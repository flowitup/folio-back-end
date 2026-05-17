"""Labor role CRUD API routes."""

from __future__ import annotations

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import jwt_required
from pydantic import ValidationError

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
    )


@labor_bp.route("/labor/roles", methods=["GET"])
@jwt_required()
def list_labor_roles():
    """List all labor roles with the suggested color palette."""
    roles = get_container().list_labor_roles_usecase.execute()
    return jsonify(
        LaborRoleListResponse(
            roles=[_role_response(r) for r in roles],
            palette=ROLE_COLOR_PALETTE,
        ).model_dump()
    )


@labor_bp.route("/labor/roles", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
def create_labor_role():
    """Create a new labor role."""
    try:
        data = CreateLaborRoleRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        role = get_container().create_labor_role_usecase.execute(
            name=data.name,
            color=data.color,
        )
    except DuplicateLaborRoleError as e:
        return _error_response("Conflict", str(e), 409)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(_role_response(role).model_dump()), 201


@labor_bp.route("/labor/roles/<role_id>", methods=["PATCH"])
@jwt_required()
@limiter.limit("10 per minute")
def update_labor_role(role_id: str):
    """Update name and/or color of a labor role."""
    try:
        data = UpdateLaborRoleRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        role = get_container().update_labor_role_usecase.execute(
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
@jwt_required()
@limiter.limit("10 per minute")
def delete_labor_role(role_id: str):
    """Delete a labor role. Workers referencing it will have role cleared."""
    try:
        get_container().delete_labor_role_usecase.execute(role_id=UUID(role_id))
    except LaborRoleNotFoundError:
        return _error_response("NotFound", f"Labor role {role_id} not found", 404)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return "", 204
