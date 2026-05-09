"""Labor entry API routes."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import jwt_required
from pydantic import ValidationError

from app.api.v1.labor import labor_bp
from app.api.v1.labor._labor_validation_error_helper import (
    _error_response,
    validation_error_response as _validation_error_response,
)
from app.api.v1.labor.schemas import (
    LogAttendanceRequest,
    UpdateAttendanceRequest,
    LaborEntryResponse,
    LaborEntryListResponse,
    LaborSummaryResponse,
    WorkerSummaryRow,
    LaborMonthlySummaryResponse,
    MonthlySummaryRowResponse,
)
from app.api.v1.projects.decorators import require_permission
from app.application.labor import (
    LogAttendanceRequest as LogAttendanceDTO,
    UpdateAttendanceRequest as UpdateAttendanceDTO,
    DeleteAttendanceRequest,
    ListLaborEntriesRequest,
    GetLaborSummaryRequest,
    GetMonthlyLaborSummaryRequest,
)
from app.domain.exceptions.labor_exceptions import (
    WorkerNotFoundError,
    LaborEntryNotFoundError,
    DuplicateEntryError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _parse_date(date_str: str) -> date:
    """Parse ISO date string to date object."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")


# Default cap on the unbounded list endpoint. Anything larger should use the
# month filter or the export endpoint. 500 keeps a year of daily entries for
# ~1.5 workers in scope, which covers the typical attendance-table view.
LABOR_ENTRIES_DEFAULT_LIMIT = 500
LABOR_ENTRIES_MAX_LIMIT = 1000


def _parse_list_limit(raw: Optional[str]) -> int:
    """Parse the optional ?limit= query arg with sensible defaults + clamp."""
    if raw is None or raw == "":
        return LABOR_ENTRIES_DEFAULT_LIMIT
    try:
        n = int(raw)
    except ValueError:
        raise ValueError("limit must be a positive integer")
    if n <= 0:
        raise ValueError("limit must be a positive integer")
    return min(n, LABOR_ENTRIES_MAX_LIMIT)


@labor_bp.route("/projects/<project_id>/labor-entries", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def list_labor_entries(project_id: str):
    """List labor entries for a project with optional filters.

    A default ``limit`` of 500 most-recent rows is applied so callers that
    omit the date filters (the attendance table's "all history" view) stay
    bounded. Pass ``?limit=N`` (1–1000) to override.
    """
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    worker_id = request.args.get("worker_id")
    limit_raw = request.args.get("limit")

    try:
        entries = get_container().list_labor_entries_usecase.execute(
            ListLaborEntriesRequest(
                project_id=UUID(project_id),
                date_from=_parse_date(date_from) if date_from else None,
                date_to=_parse_date(date_to) if date_to else None,
                worker_id=UUID(worker_id) if worker_id else None,
                limit=_parse_list_limit(limit_raw),
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(
        LaborEntryListResponse(
            entries=[
                LaborEntryResponse(
                    id=e.id,
                    worker_id=e.worker_id,
                    worker_name=e.worker_name,
                    date=e.date,
                    amount_override=e.amount_override,
                    effective_cost=e.effective_cost,
                    note=e.note,
                    shift_type=e.shift_type,
                    supplement_hours=e.supplement_hours,
                    created_at=e.created_at,
                )
                for e in entries
            ],
            total=len(entries),
        ).model_dump()
    )


@labor_bp.route("/projects/<project_id>/labor-entries", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
def log_attendance(project_id: str):
    """Log daily attendance for a worker."""
    try:
        data = LogAttendanceRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        result = get_container().log_attendance_usecase.execute(
            LogAttendanceDTO(
                project_id=UUID(project_id),
                worker_id=UUID(data.worker_id),
                date=_parse_date(data.date),
                amount_override=Decimal(str(data.amount_override)) if data.amount_override else None,
                note=data.note,
                shift_type=data.shift_type,
                supplement_hours=data.supplement_hours,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotFoundError as e:
        return _error_response("NotFound", str(e), 404)
    except DuplicateEntryError as e:
        return _error_response("Conflict", str(e), 409)

    return (
        jsonify(
            {
                "id": result.id,
                "worker_id": result.worker_id,
                "date": result.date,
                "shift_type": result.shift_type,
                "supplement_hours": result.supplement_hours,
                "amount_override": result.amount_override,
                "note": result.note,
                "created_at": result.created_at,
            }
        ),
        201,
    )


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
def update_attendance(project_id: str, entry_id: str):
    """Update an existing labor entry."""
    try:
        data = UpdateAttendanceRequest(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        result = get_container().update_attendance_usecase.execute(
            UpdateAttendanceDTO(
                entry_id=UUID(entry_id),
                amount_override=Decimal(str(data.amount_override)) if data.amount_override is not None else None,
                note=data.note,
                shift_type=data.shift_type,
                supplement_hours=data.supplement_hours,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except LaborEntryNotFoundError:
        return _error_response("NotFound", f"Labor entry {entry_id} not found", 404)

    return jsonify(
        {
            "id": result.id,
            "worker_id": result.worker_id,
            "date": result.date,
            "shift_type": result.shift_type,
            "supplement_hours": result.supplement_hours,
            "amount_override": result.amount_override,
            "note": result.note,
            "created_at": result.created_at,
        }
    )


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>", methods=["DELETE"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
def delete_attendance(project_id: str, entry_id: str):
    """Delete a labor entry."""
    try:
        get_container().delete_attendance_usecase.execute(DeleteAttendanceRequest(entry_id=UUID(entry_id)))
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except LaborEntryNotFoundError:
        return _error_response("NotFound", f"Labor entry {entry_id} not found", 404)

    return "", 204


@labor_bp.route("/projects/<project_id>/labor-summary", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def get_labor_summary(project_id: str):
    """Get aggregated labor summary for a project."""
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    try:
        result = get_container().get_labor_summary_usecase.execute(
            GetLaborSummaryRequest(
                project_id=UUID(project_id),
                date_from=_parse_date(date_from) if date_from else None,
                date_to=_parse_date(date_to) if date_to else None,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(
        LaborSummaryResponse(
            rows=[
                WorkerSummaryRow(
                    worker_id=r.worker_id,
                    worker_name=r.worker_name,
                    days_worked=r.days_worked,
                    total_cost=r.total_cost,
                    banked_hours=r.banked_hours,
                    bonus_full_days=r.bonus_full_days,
                    bonus_half_days=r.bonus_half_days,
                    bonus_cost=r.bonus_cost,
                )
                for r in result.rows
            ],
            total_days=result.total_days,
            total_cost=result.total_cost,
            total_banked_hours=result.total_banked_hours,
            total_bonus_days=result.total_bonus_days,
            total_bonus_cost=result.total_bonus_cost,
        ).model_dump()
    )


@labor_bp.route("/projects/<project_id>/labor-monthly-summary", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def get_labor_monthly_summary(project_id: str):
    """Per-month rollup of labor totals across all workers on a project.

    Powers the Summary tab's all-history view: a list of (year, month) rows
    ordered most-recent first. The Summary tab uses this when no specific
    month filter is active; picking a month falls back to the per-worker
    /labor-summary endpoint.
    """
    try:
        result = get_container().get_monthly_labor_summary_usecase.execute(
            GetMonthlyLaborSummaryRequest(project_id=UUID(project_id))
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(
        LaborMonthlySummaryResponse(
            rows=[
                MonthlySummaryRowResponse(
                    year=r.year,
                    month=r.month,
                    total_days=r.total_days,
                    total_cost=r.total_cost,
                )
                for r in result.rows
            ],
        ).model_dump()
    )
