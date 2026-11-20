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
from app.api.v1.labor.month_closure_audit import rate_change_closed_month_writes, with_closed_months
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
from app.api.v1.projects.labor_scope import labor_scope_for, restricted_forbidden
from app.application.labor.delete_worker_rate_change import DeleteWorkerRateChangeRequest
from app.application.labor.list_worker_rate_changes import ListWorkerRateChangesRequest
from app.application.labor.set_worker_rate_change import SetWorkerRateChangeRequest
from app.domain.exceptions.labor_exceptions import (
    InvalidRateChangeError,
    RateChangeNotFoundError,
    WorkerNotFoundError,
)
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.infrastructure.rate_limiter import limiter, request_succeeded
from wiring import get_container


def _find_rate_change(rc_id: str):
    """The rate-change row about to be deleted (for the closed-month record), or None."""
    try:
        repo = get_container().worker_rate_change_repository
        return repo.find_by_id(UUID(rc_id)) if repo is not None else None
    except Exception:
        return None


def _next_effective_date(worker_id: str, after):
    """Effective date of the worker's first rate change strictly after ``after`` (None if none)."""
    try:
        repo = get_container().worker_rate_change_repository
        if repo is None:
            return None
        later = [rc.effective_date for rc in repo.list_by_worker(UUID(worker_id)) if rc.effective_date > after]
        return min(later) if later else None
    except Exception:
        return None


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
    if not labor_scope_for(project_id).allows_worker(worker_id):
        return restricted_forbidden()
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
@limiter.limit("30 per minute", key_func=jwt_user_key, deduct_when=request_succeeded)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def set_worker_rate_change(project_id: str, worker_id: str):
    """Create or update an effective-dated rate change for a worker (upsert by date)."""
    try:
        data = CreateRateChangeRequest.model_validate(request.get_json())
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

    closed = rate_change_closed_month_writes(
        project_id,
        worker_id,
        data.effective_date,
        _next_effective_date(worker_id, data.effective_date),
        "rate_changed",
        dto.id,
    )
    return jsonify(with_closed_months(_rc_response(dto).model_dump(), closed)), 201


@labor_bp.route(
    "/projects/<project_id>/workers/<worker_id>/rate-changes/<rc_id>",
    methods=["DELETE"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key, deduct_when=request_succeeded)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def delete_worker_rate_change(project_id: str, worker_id: str, rc_id: str):
    """Delete a specific rate-change row."""
    doomed = _find_rate_change(rc_id)
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

    if doomed is not None:
        # The deleted rate's days fall back to the previous rate up to the next change.
        rate_change_closed_month_writes(
            project_id,
            worker_id,
            doomed.effective_date,
            _next_effective_date(worker_id, doomed.effective_date),
            "rate_change_deleted",
            doomed.id,
        )
    return "", 204
