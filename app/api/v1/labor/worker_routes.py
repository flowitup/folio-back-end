"""Worker API routes."""

from decimal import Decimal
from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import jwt_required
from pydantic import ValidationError

from app.api.v1.labor import labor_bp
from app.api.v1.labor.schemas import (
    CreateWorkerRequest,
    UpdateWorkerRequest,
    WorkerResponse,
    WorkerListResponse,
    ErrorResponse,
)
from app.api.v1.projects.decorators import require_permission
from app.application.labor import (
    CreateWorkerRequest as CreateWorkerDTO,
    UpdateWorkerRequest as UpdateWorkerDTO,
    DeleteWorkerRequest as DeleteWorkerDTO,
    ListWorkersRequest,
)
from app.domain.exceptions.labor_exceptions import (
    WorkerNotFoundError,
    InvalidWorkerDataError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    """Create standardized error response."""
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def _validation_error_response(e: ValidationError) -> Tuple[Response, int]:
    """Create validation error response from Pydantic error."""
    error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
    return _error_response("ValidationError", f"Invalid input: {', '.join(str(f) for f in error_fields)}", 400)


def _worker_response(w) -> WorkerResponse:
    """Convert worker entity to response schema."""
    return WorkerResponse(
        id=w.id,
        project_id=w.project_id,
        name=w.name,
        phone=w.phone,
        daily_rate=w.daily_rate,
        is_active=w.is_active,
        created_at=w.created_at,
    )


@labor_bp.route("/projects/<project_id>/workers", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def list_workers(project_id: str):
    """List workers for a project."""
    try:
        workers = get_container().list_workers_usecase.execute(ListWorkersRequest(project_id=UUID(project_id)))
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(WorkerListResponse(workers=[_worker_response(w) for w in workers], total=len(workers)).model_dump())


@labor_bp.route("/projects/<project_id>/workers", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
def create_worker(project_id: str):
    """Create a new worker for a project."""
    try:
        data = CreateWorkerRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        result = get_container().create_worker_usecase.execute(
            CreateWorkerDTO(
                project_id=UUID(project_id),
                name=data.name,
                daily_rate=Decimal(str(data.daily_rate)),
                phone=data.phone,
            )
        )
    except (ValueError, InvalidWorkerDataError) as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(_worker_response(result).model_dump()), 201


@labor_bp.route("/projects/<project_id>/workers/<worker_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
def update_worker(project_id: str, worker_id: str):
    """Update an existing worker."""
    try:
        data = UpdateWorkerRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        result = get_container().update_worker_usecase.execute(
            UpdateWorkerDTO(
                worker_id=UUID(worker_id),
                name=data.name,
                phone=data.phone,
                daily_rate=Decimal(str(data.daily_rate)) if data.daily_rate else None,
            )
        )
    except (ValueError, InvalidWorkerDataError) as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotFoundError:
        return _error_response("NotFound", f"Worker {worker_id} not found", 404)

    return jsonify(_worker_response(result).model_dump())


@labor_bp.route("/projects/<project_id>/workers/<worker_id>", methods=["DELETE"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
def delete_worker(project_id: str, worker_id: str):
    """Soft delete a worker (deactivate)."""
    try:
        get_container().delete_worker_usecase.execute(DeleteWorkerDTO(worker_id=UUID(worker_id)))
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotFoundError:
        return _error_response("NotFound", f"Worker {worker_id} not found", 404)

    return "", 204
