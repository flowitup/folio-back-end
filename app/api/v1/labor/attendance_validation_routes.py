"""Attendance validation routes — worker self-log, manager validate / reject."""

import os
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api.openapi import openapi_doc
from app.api.v1.labor import labor_bp
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.labor._labor_validation_error_helper import (
    _error_response,
    parse_iso_date as _parse_date,
    validation_error_response as _validation_error_response,
)
from app.api.v1.labor.schemas import (
    OwnAttendanceEntryResponse,
    SelfEditAttendanceRequest,
    SelfLogAttendanceRequest,
    SelfLoggedEntryResponse,
    ValidatedEntryResponse,
)
from app.api.v1.projects.decorators import require_permission, require_project_access
from app.application.labor import (
    RejectAttendanceRequest as RejectAttendanceDTO,
    SubmitOwnAttendanceRequest as SubmitOwnAttendanceDTO,
    ValidateAttendanceRequest as ValidateAttendanceDTO,
)
from app.application.labor.edit_own_attendance import (
    DecideAttendanceChangeRequest as DecideChangeDTO,
    EditOwnAttendanceRequest as EditOwnAttendanceDTO,
)
from app.domain.exceptions.labor_exceptions import (
    AttendanceAlreadyValidatedError,
    AttendanceDateOutOfRangeError,
    DuplicateEntryError,
    InvalidLaborEntryError,
    LaborEntryNotFoundError,
    NoChangeRequestError,
    WorkerNotFoundError,
    WorkerNotLinkedError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# Workers may catch up on missed days for about a month; every such day stays pending
# until a manager validates it, so a wide window costs nothing in trust.
DEFAULT_SELF_ATTENDANCE_MAX_BACKDATE_DAYS = 31


def _max_backdate_days() -> int:
    """How far back a worker may self-log. Env-tunable for ops (SELF_ATTENDANCE_MAX_BACKDATE_DAYS)."""
    try:
        return max(
            int(os.environ.get("SELF_ATTENDANCE_MAX_BACKDATE_DAYS", str(DEFAULT_SELF_ATTENDANCE_MAX_BACKDATE_DAYS))), 0
        )
    except ValueError:
        return DEFAULT_SELF_ATTENDANCE_MAX_BACKDATE_DAYS


@labor_bp.route("/projects/<project_id>/labor-entries/self", methods=["POST"])
@openapi_doc(
    summary="Log my own attendance for a day (pending until a manager validates)",
    request=SelfLogAttendanceRequest,
    responses={201: SelfLoggedEntryResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
@require_permission("project:log_own_attendance")
@require_project_access(write=False)
def submit_own_attendance(project_id: str):
    """The calling user must be linked to a worker on this project (workers.user_id)."""
    try:
        data = SelfLogAttendanceRequest(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    usecase = get_container().submit_own_attendance_usecase
    if usecase is None:
        raise RuntimeError("submit_own_attendance_usecase not wired in container")

    try:
        result = usecase.execute(
            SubmitOwnAttendanceDTO(
                project_id=UUID(project_id),
                user_id=UUID(str(get_jwt_identity())),
                date=_parse_date(data.date),
                shift_type=data.shift_type,
                supplement_hours=data.supplement_hours,
                note=data.note,
                max_backdate_days=_max_backdate_days(),
            )
        )
    except (ValueError, AttendanceDateOutOfRangeError) as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotLinkedError as e:
        return _error_response("WorkerNotLinked", str(e), 404)
    except WorkerNotFoundError as e:
        return _error_response("NotFound", str(e), 404)
    except DuplicateEntryError as e:
        return _error_response("Conflict", str(e), 409)

    return jsonify(SelfLoggedEntryResponse(**result.__dict__).model_dump()), 201


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>/validate", methods=["POST"])
@openapi_doc(
    summary="Validate a worker-submitted attendance entry (idempotent)",
    responses={200: ValidatedEntryResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def validate_attendance(project_id: str, entry_id: str):
    """Flip a pending entry to validated; it then counts in summaries, exports and budgets."""
    usecase = get_container().validate_attendance_usecase
    if usecase is None:
        raise RuntimeError("validate_attendance_usecase not wired in container")

    try:
        result = usecase.execute(
            ValidateAttendanceDTO(
                entry_id=UUID(entry_id),
                project_id=UUID(project_id),
                validator_user_id=UUID(str(get_jwt_identity())),
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except LaborEntryNotFoundError:
        return _error_response("NotFound", f"Labor entry {entry_id} not found", 404)

    return jsonify(ValidatedEntryResponse(**result.__dict__).model_dump())


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>/reject", methods=["POST"])
@openapi_doc(
    summary="Reject a pending attendance entry (deletes it; 409 once validated)",
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def reject_attendance(project_id: str, entry_id: str):
    """Rejecting removes the row so the worker sees the day as unlogged and can resubmit."""
    usecase = get_container().reject_attendance_usecase
    if usecase is None:
        raise RuntimeError("reject_attendance_usecase not wired in container")

    try:
        usecase.execute(
            RejectAttendanceDTO(
                entry_id=UUID(entry_id),
                project_id=UUID(project_id),
                actor_user_id=UUID(str(get_jwt_identity())),
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except LaborEntryNotFoundError:
        return _error_response("NotFound", f"Labor entry {entry_id} not found", 404)
    except AttendanceAlreadyValidatedError as e:
        return _error_response("Conflict", str(e), 409)

    return "", 204


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>/self", methods=["PUT"])
@openapi_doc(
    summary="Edit one of my own days: a pending day is updated, a validated day gets a change request",
    request=SelfEditAttendanceRequest,
    responses={200: OwnAttendanceEntryResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("20 per minute", key_func=jwt_user_key)
@require_permission("project:log_own_attendance")
@require_project_access(write=False)
def edit_own_attendance(project_id: str, entry_id: str):
    """The entry must belong to the worker linked to the caller (404 otherwise)."""
    try:
        data = SelfEditAttendanceRequest(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)
    usecase = get_container().edit_own_attendance_usecase
    if usecase is None:
        raise RuntimeError("edit_own_attendance_usecase not wired in container")
    try:
        result = usecase.execute(
            EditOwnAttendanceDTO(
                project_id=UUID(project_id),
                user_id=UUID(str(get_jwt_identity())),
                entry_id=UUID(entry_id),
                shift_type=data.shift_type,
                supplement_hours=data.supplement_hours,
                note=data.note,
            )
        )
    except (ValueError, InvalidLaborEntryError) as e:
        return _error_response("ValidationError", str(e), 400)
    except WorkerNotLinkedError as e:
        return _error_response("WorkerNotLinked", str(e), 404)
    except (WorkerNotFoundError, LaborEntryNotFoundError):
        return _error_response("NotFound", f"Labor entry {entry_id} not found", 404)
    return jsonify(OwnAttendanceEntryResponse(**result.__dict__).model_dump())


def _decide_change(project_id: str, entry_id: str, approve: bool):
    usecase = get_container().decide_attendance_change_usecase
    if usecase is None:
        raise RuntimeError("decide_attendance_change_usecase not wired in container")
    try:
        result = usecase.execute(
            DecideChangeDTO(
                entry_id=UUID(entry_id),
                project_id=UUID(project_id),
                actor_user_id=UUID(str(get_jwt_identity())),
                approve=approve,
            )
        )
    except (ValueError, InvalidLaborEntryError) as e:
        return _error_response("ValidationError", str(e), 400)
    except LaborEntryNotFoundError:
        return _error_response("NotFound", f"Labor entry {entry_id} not found", 404)
    except NoChangeRequestError as e:
        return _error_response("Conflict", str(e), 409)
    return jsonify(OwnAttendanceEntryResponse(**result.__dict__).model_dump())


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>/change/validate", methods=["POST"])
@openapi_doc(
    summary="Apply a worker's change request to the day (409 when none is open)",
    responses={200: OwnAttendanceEntryResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def validate_attendance_change(project_id: str, entry_id: str):
    return _decide_change(project_id, entry_id, approve=True)


@labor_bp.route("/projects/<project_id>/labor-entries/<entry_id>/change/reject", methods=["POST"])
@openapi_doc(
    summary="Drop a worker's change request; the validated day stays as it is (409 when none is open)",
    responses={200: OwnAttendanceEntryResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def reject_attendance_change(project_id: str, entry_id: str):
    return _decide_change(project_id, entry_id, approve=False)
