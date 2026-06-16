"""Worker API routes."""

from decimal import Decimal
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
from app.api.v1.labor.schemas import (
    CreateWorkerRequest,
    UpdateWorkerRequest,
    WorkerResponse,
    WorkerListResponse,
)
from app.api.v1.projects.decorators import require_permission, require_project_access
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


def _worker_response(w) -> WorkerResponse:
    """Convert worker entity/summary to response schema.

    person_id / person_name / person_phone surface the joined identity from
    cook 1d-ii-a. They are None for workers not yet linked (pre-backfill).
    role_id / role_name / role_color surface the joined LaborRole identity.

    current_daily_rate is resolved by ListWorkersUseCase from the rate-change
    timeline (latest change <= today, else base). On create/update responses
    the use case is not invoked, so we fall back to daily_rate — the FE
    re-fetches the list to display the resolved rate.
    """
    # WorkerSummary (list path) carries current_daily_rate; Worker entity
    # (create/update path) carries the transient field set to None until
    # list_workers resolves it.
    _current = getattr(w, "current_daily_rate", None)
    return WorkerResponse(
        id=w.id,
        project_id=w.project_id,
        name=w.name,
        phone=w.phone,
        daily_rate=w.daily_rate,
        is_active=w.is_active,
        created_at=w.created_at,
        person_id=str(w.person_id) if w.person_id else None,
        person_name=w.person_name,
        person_phone=w.person_phone,
        role_id=str(w.role_id) if w.role_id else None,
        role_name=w.role_name,
        role_color=w.role_color,
        current_daily_rate=float(_current) if _current is not None else float(w.daily_rate),
    )


@labor_bp.route("/projects/<project_id>/workers", methods=["GET"])
@openapi_doc(summary="List workers for a project", tags=["labor"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_workers(project_id: str):
    """List workers for a project."""
    try:
        workers = get_container().list_workers_usecase.execute(ListWorkersRequest(project_id=UUID(project_id)))
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(WorkerListResponse(workers=[_worker_response(w) for w in workers], total=len(workers)).model_dump())


@labor_bp.route("/projects/<project_id>/workers", methods=["POST"])
@openapi_doc(
    summary="Create a new worker for a project",
    request=CreateWorkerRequest,
    responses={201: WorkerResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
@require_project_access(write=False)
def create_worker(project_id: str):
    """Create a new worker for a project."""
    try:
        data = CreateWorkerRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        # JWT subject identifies the caller — required when the use case
        # has to create a Person inline (no person_id supplied). The
        # use case ignores it when person_id is set.
        creator_id = UUID(str(get_jwt_identity()))
    except (TypeError, ValueError):
        return _error_response("ValidationError", "Invalid JWT identity", 401)

    try:
        result = get_container().create_worker_usecase.execute(
            CreateWorkerDTO(
                project_id=UUID(project_id),
                name=data.name,
                daily_rate=Decimal(str(data.daily_rate)),
                phone=data.phone,
                person_id=UUID(data.person_id) if data.person_id else None,
                created_by_user_id=creator_id,
                role_id=UUID(data.role_id) if data.role_id else None,
            )
        )
    except (ValueError, InvalidWorkerDataError) as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(_worker_response(result).model_dump()), 201


@labor_bp.route("/projects/<project_id>/workers/<worker_id>", methods=["PUT"])
@openapi_doc(
    summary="Update an existing worker",
    request=UpdateWorkerRequest,
    responses={200: WorkerResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
@require_project_access(write=False)
def update_worker(project_id: str, worker_id: str):
    """Update an existing worker."""
    try:
        data = UpdateWorkerRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        # Only forward role_id when the client explicitly sent the key
        # — distinguishes "clear" (sent: null) from "leave unchanged" (omitted).
        # daily_rate is NOT forwarded: base rate is locked at creation time.
        update_kwargs = dict(
            worker_id=UUID(worker_id),
            name=data.name,
            phone=data.phone,
        )
        if "role_id" in data.model_fields_set:
            update_kwargs["role_id"] = UUID(data.role_id) if data.role_id else None
        result = get_container().update_worker_usecase.execute(UpdateWorkerDTO(**update_kwargs))
    except (ValueError, InvalidWorkerDataError) as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotFoundError:
        return _error_response("NotFound", f"Worker {worker_id} not found", 404)

    return jsonify(_worker_response(result).model_dump())


@labor_bp.route("/projects/<project_id>/workers/<worker_id>", methods=["DELETE"])
@openapi_doc(summary="Soft delete a worker (deactivate)", tags=["labor"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
@require_project_access(write=False)
def delete_worker(project_id: str, worker_id: str):
    """Soft delete a worker (deactivate)."""
    try:
        get_container().delete_worker_usecase.execute(DeleteWorkerDTO(worker_id=UUID(worker_id)))
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotFoundError:
        return _error_response("NotFound", f"Worker {worker_id} not found", 404)

    return "", 204
