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
    BulkLogAttendanceRequest,
    BulkLogAttendanceResponse,
    UpdateAttendanceRequest,
    LaborEntryResponse,
    LaborEntryListResponse,
    LaborSummaryResponse,
    WorkerSummaryRow,
    LaborMonthlySummaryResponse,
    MonthlySummaryRowResponse,
    MonthlyWorkerSubRowResponse,
    CrossProjectConflictsResponse,
    CrossProjectConflictResponse,
    CrossProjectConflictEntryResponse,
)
from app.api.v1.projects.decorators import require_permission, require_project_access
from app.application.labor import (
    LogAttendanceRequest as LogAttendanceDTO,
    BulkLogAttendanceRequest as BulkLogAttendanceDTO,
    BulkLogAttendanceEntry as BulkLogAttendanceEntryDTO,
    ConflictsNotAcknowledgedError,
    UpdateAttendanceRequest as UpdateAttendanceDTO,
    DeleteAttendanceRequest,
    FindCrossProjectConflictsRequest,
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
@require_project_access(write=False)
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
                    worker_avatar_url=getattr(e, "worker_avatar_url", None),
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
@require_project_access(write=False)
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


@labor_bp.route("/projects/<project_id>/labor-entries/conflicts", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def get_cross_project_conflicts(project_id: str):
    """Return cross-project labor conflicts on a given date (Phase 4).

    A conflict surfaces when a Person who has an active Worker on this
    project also has an active Worker on another project in the same
    company, with a labor_entry on the requested ``?date=`` query arg.

    Optional ``?person_ids=uuid1,uuid2`` narrows the scan to specific
    Persons. The endpoint *informs* — it never blocks; saving through
    /labor-entries/bulk handles the warn/acknowledge flow.
    """
    date_str = request.args.get("date")
    if not date_str:
        return _error_response("ValidationError", "Missing ?date=YYYY-MM-DD", 400)

    person_ids_raw = request.args.get("person_ids")
    person_ids: Optional[list[UUID]] = None
    if person_ids_raw:
        try:
            person_ids = [UUID(s) for s in person_ids_raw.split(",") if s]
        except ValueError:
            return _error_response("ValidationError", "person_ids must be a comma list of UUIDs", 400)

    try:
        result = get_container().find_cross_project_conflicts_usecase.execute(
            FindCrossProjectConflictsRequest(
                project_id=UUID(project_id),
                date=_parse_date(date_str),
                person_ids=person_ids,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(
        CrossProjectConflictsResponse(
            conflicts=[
                CrossProjectConflictResponse(
                    person_id=str(c.person_id),
                    person_name=c.person_name,
                    entries=[
                        CrossProjectConflictEntryResponse(
                            project_id=str(e.project_id),
                            project_name=e.project_name,
                            shift_type=e.shift_type,
                            supplement_hours=e.supplement_hours,
                        )
                        for e in c.entries
                    ],
                )
                for c in result.conflicts
            ],
        ).model_dump()
    )


@labor_bp.route("/projects/<project_id>/labor-entries/bulk", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
@require_project_access(write=False)
def bulk_log_attendance(project_id: str):
    """Bulk-log attendance for N workers on a single date (cook 3a).

    Atomic: all rows persisted in the same SQLAlchemy session; existing
    (worker, date) entries are silently skipped and returned in
    `skipped_worker_ids` so the FE can render a "3 logged, 1 skipped"
    toast. Cross-project conflict warn is Phase 4.
    """
    try:
        data = BulkLogAttendanceRequest(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        result = get_container().bulk_log_attendance_usecase.execute(
            BulkLogAttendanceDTO(
                project_id=UUID(project_id),
                date=_parse_date(data.date),
                entries=[
                    BulkLogAttendanceEntryDTO(
                        worker_id=UUID(e.worker_id),
                        shift_type=e.shift_type,
                        supplement_hours=e.supplement_hours,
                        amount_override=(Decimal(str(e.amount_override)) if e.amount_override is not None else None),
                        note=e.note,
                    )
                    for e in data.entries
                ],
                acknowledge_conflicts=data.acknowledge_conflicts,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotFoundError as e:
        return _error_response("NotFound", str(e), 404)
    except ConflictsNotAcknowledgedError as e:
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": (
                        "Cross-project conflicts exist; resend with " "acknowledge_conflicts=true to override."
                    ),
                    "conflicts": [
                        CrossProjectConflictResponse(
                            person_id=str(c.person_id),
                            person_name=c.person_name,
                            entries=[
                                CrossProjectConflictEntryResponse(
                                    project_id=str(ent.project_id),
                                    project_name=ent.project_name,
                                    shift_type=ent.shift_type,
                                    supplement_hours=ent.supplement_hours,
                                )
                                for ent in c.entries
                            ],
                        ).model_dump()
                        for c in e.conflicts
                    ],
                }
            ),
            409,
        )

    return (
        jsonify(
            BulkLogAttendanceResponse(
                created=result.created,
                skipped_worker_ids=result.skipped_worker_ids,
            ).model_dump()
        ),
        201,
    )


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("10 per minute")
@require_permission("project:manage_labor")
@require_project_access(write=False)
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
                project_id=UUID(project_id),
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
@require_project_access(write=False)
def delete_attendance(project_id: str, entry_id: str):
    """Delete a labor entry."""
    try:
        get_container().delete_attendance_usecase.execute(
            DeleteAttendanceRequest(entry_id=UUID(entry_id), project_id=UUID(project_id))
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except LaborEntryNotFoundError:
        return _error_response("NotFound", f"Labor entry {entry_id} not found", 404)

    return "", 204


@labor_bp.route("/projects/<project_id>/labor-summary", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
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
@require_project_access(write=False)
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
                    workers=[
                        MonthlyWorkerSubRowResponse(
                            worker_id=w.worker_id,
                            worker_name=w.worker_name,
                            days_worked=w.days_worked,
                            total_cost=w.total_cost,
                        )
                        for w in r.workers
                    ],
                )
                for r in result.rows
            ],
        ).model_dump()
    )
