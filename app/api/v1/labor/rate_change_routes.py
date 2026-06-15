"""Worker rate-change API routes.

Routes live under /projects/<project_id>/workers/<worker_id>/rate-changes.
Permission model mirrors worker_routes.py exactly:
  - read:   project:read  + require_project_access(write=False)
  - write:  project:manage_labor + require_project_access(write=False) + rate-limit
"""

from decimal import Decimal
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import jwt_required
from pydantic import ValidationError

from app.api.v1.labor import labor_bp
from app.api.v1.labor._labor_validation_error_helper import (
    _error_response,
    validation_error_response as _validation_error_response,
)
from app.api.v1.labor.rate_change_schemas import (
    CreateRateChangeRequest,
    RateChangeListResponse,
    RateChangeResponse,
)
from app.api.v1.projects.decorators import require_permission, require_project_access
from app.application.labor.delete_worker_rate_change import DeleteWorkerRateChangeRequest
from app.application.labor.list_worker_rate_changes import ListWorkerRateChangesRequest
from app.application.labor.set_worker_rate_change import SetWorkerRateChangeRequest
from app.domain.exceptions.labor_exceptions import (
    InvalidRateChangeError,
    RateChangeNotFoundError,
    WorkerNotFoundError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _rc_response(dto) -> RateChangeResponse:
    """Convert a RateChangeDTO to the API response schema."""
    return RateChangeResponse(
        id=dto.id,
        worker_id=dto.worker_id,
        effective_date=dto.effective_date,
        daily_rate=dto.daily_rate,
        created_at=dto.created_at,
    )


@labor_bp.route("/projects/<project_id>/workers/<worker_id>/rate-changes", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_worker_rate_changes(project_id: str, worker_id: str):
    """List all effective-dated rate changes for a worker, newest first."""
    try:
        dtos = get_container().list_worker_rate_changes_usecase.execute(
            ListWorkerRateChangesRequest(
                project_id=UUID(project_id),
                worker_id=UUID(worker_id),
            )
        )
    except WorkerNotFoundError:
        return _error_response("NotFound", f"Worker {worker_id} not found", 404)
    except ValueError as exc:
        return _error_response("ValidationError", str(exc), 400)

    return jsonify(RateChangeListResponse(rate_changes=[_rc_response(d) for d in dtos]).model_dump())


@labor_bp.route("/projects/<project_id>/workers/<worker_id>/rate-changes", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
@require_project_access(write=False)
def set_worker_rate_change(project_id: str, worker_id: str):
    """Create or update an effective-dated rate change for a worker (upsert by date)."""
    try:
        data = CreateRateChangeRequest(**request.get_json())
    except ValidationError as exc:
        return _validation_error_response(exc)

    try:
        dto = get_container().set_worker_rate_change_usecase.execute(
            SetWorkerRateChangeRequest(
                project_id=UUID(project_id),
                worker_id=UUID(worker_id),
                effective_date=data.effective_date,
                daily_rate=Decimal(str(data.daily_rate)),
            )
        )
    except WorkerNotFoundError:
        return _error_response("NotFound", f"Worker {worker_id} not found", 404)
    except (InvalidRateChangeError, ValueError) as exc:
        return _error_response("ValidationError", str(exc), 400)

    return jsonify(_rc_response(dto).model_dump()), 201


@labor_bp.route(
    "/projects/<project_id>/workers/<worker_id>/rate-changes/<rc_id>",
    methods=["DELETE"],
)
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
@require_project_access(write=False)
def delete_worker_rate_change(project_id: str, worker_id: str, rc_id: str):
    """Delete a specific rate-change row."""
    try:
        get_container().delete_worker_rate_change_usecase.execute(
            DeleteWorkerRateChangeRequest(
                project_id=UUID(project_id),
                worker_id=UUID(worker_id),
                rc_id=UUID(rc_id),
            )
        )
    except WorkerNotFoundError:
        return _error_response("NotFound", f"Worker {worker_id} not found", 404)
    except RateChangeNotFoundError:
        return _error_response("NotFound", f"Rate change {rc_id} not found", 404)
    except ValueError as exc:
        return _error_response("ValidationError", str(exc), 400)

    return "", 204
