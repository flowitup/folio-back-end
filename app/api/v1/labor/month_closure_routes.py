"""Closed labor months ("Chốt tháng", owner decision QĐ-10).

GET    /projects/<project_id>/labor-month-closures[?month=YYYY-MM]
       → 200 { "closures": [ {month, closed_at, closed_by_*, edited_after_close,
                               last_edited_*, edits: [...] }, ... ] }   newest month first
POST   /projects/<project_id>/labor-month-closures  { "month": "YYYY-MM" }
       → 201 the closure (200 with the existing one when the month is already closed)
       → 400 month_in_future for a month that has not started
DELETE /projects/<project_id>/labor-month-closures/<YYYY-MM>
       → 204 reopened; 404 when the month is not closed

Closing is a marker, not a lock: writes into a closed month keep working and are
recorded (see ``month_closure_audit``). Closing and reopening need
``project:manage_labor`` (company admin, assigned manager, D8 grantee; a view_pay
deny removes it). Reading needs the full labor view (manage_labor or view_pay):
restricted members get 403, like every other project-wide pay view.
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import BaseModel, Field, ValidationError

from app.api.openapi import openapi_doc
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.labor import labor_bp
from app.api.v1.labor._labor_validation_error_helper import (
    _error_response,
    validation_error_response as _validation_error_response,
)
from app.api.v1.projects.decorators import require_permission, require_project_access
from app.api.v1.projects.labor_scope import require_full_project_view
from app.application.labor.labor_month_closure_usecases import (
    CloseLaborMonthRequest,
    LaborMonthClosureDetail,
    ListLaborMonthClosuresRequest,
    MonthInFutureError,
    MonthNotClosedError,
    ReopenLaborMonthRequest,
)
from app.infrastructure.rate_limiter import limiter, request_succeeded
from wiring import get_container

_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class CloseLaborMonthSchema(BaseModel):
    month: str = Field(..., pattern=_MONTH_PATTERN)


class ClosureEditResponse(BaseModel):
    kind: str
    edited_at: Optional[str]
    user_id: Optional[str]
    user_name: Optional[str]
    entry_id: Optional[str]
    worker_id: Optional[str]
    worker_name: Optional[str]
    entry_date: Optional[str]


class LaborMonthClosureResponse(BaseModel):
    project_id: str
    month: str
    closed_at: Optional[str]
    closed_by_user_id: Optional[str]
    closed_by_name: Optional[str]
    edited_after_close: int
    last_edited_at: Optional[str]
    last_edited_by_user_id: Optional[str]
    last_edited_by_name: Optional[str]
    edits: List[ClosureEditResponse]


class LaborMonthClosureListResponse(BaseModel):
    closures: List[LaborMonthClosureResponse]


def _parse_month(month_str: str) -> date:
    try:
        return datetime.strptime(month_str, "%Y-%m").date()
    except ValueError:
        raise ValueError(f"Invalid month format: {month_str}. Expected YYYY-MM")


def _response(d: LaborMonthClosureDetail) -> LaborMonthClosureResponse:
    return LaborMonthClosureResponse(
        project_id=d.project_id,
        month=d.month,
        closed_at=d.closed_at,
        closed_by_user_id=d.closed_by_user_id,
        closed_by_name=d.closed_by_name,
        edited_after_close=d.edited_after_close,
        last_edited_at=d.last_edited_at,
        last_edited_by_user_id=d.last_edited_by_user_id,
        last_edited_by_name=d.last_edited_by_name,
        edits=[ClosureEditResponse(**e.__dict__) for e in d.edits],
    )


def _usecase(name: str):
    usecase = getattr(get_container(), name, None)
    if usecase is None:
        raise RuntimeError(f"{name} not wired in container")
    return usecase


@labor_bp.route("/projects/<project_id>/labor-month-closures", methods=["GET"])
@openapi_doc(
    summary="List the project's closed labor months, with the edits made after closing",
    responses={200: LaborMonthClosureListResponse},
    tags=["labor"],
)
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
@require_full_project_view
def list_labor_month_closures(project_id: str):
    month_str = request.args.get("month")
    try:
        month = _parse_month(month_str) if month_str else None
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    closures = _usecase("list_labor_month_closures_usecase").execute(
        ListLaborMonthClosuresRequest(project_id=UUID(project_id), month=month)
    )
    return jsonify(LaborMonthClosureListResponse(closures=[_response(c) for c in closures]).model_dump())


@labor_bp.route("/projects/<project_id>/labor-month-closures", methods=["POST"])
@openapi_doc(
    summary="Close a labor month (marker only: later edits still save and are recorded)",
    request=CloseLaborMonthSchema,
    responses={201: LaborMonthClosureResponse},
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key, deduct_when=request_succeeded)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def close_labor_month(project_id: str):
    try:
        data = CloseLaborMonthSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return _validation_error_response(e)
    user_id = get_jwt_identity()
    try:
        detail, created = _usecase("close_labor_month_usecase").execute(
            CloseLaborMonthRequest(
                project_id=UUID(project_id),
                month=_parse_month(data.month),
                user_id=UUID(str(user_id)) if user_id else None,
            )
        )
    except MonthInFutureError:
        return _error_response("month_in_future", "A month that has not started cannot be closed", 400)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    return jsonify(_response(detail).model_dump()), 201 if created else 200


@labor_bp.route("/projects/<project_id>/labor-month-closures/<month>", methods=["DELETE"])
@openapi_doc(summary="Reopen a closed labor month (undo the closure)", tags=["labor"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key, deduct_when=request_succeeded)
@require_permission("project:manage_labor")
@require_project_access(write=False)
def reopen_labor_month(project_id: str, month: str):
    try:
        if not month or len(month) != 7:
            raise ValueError(f"Invalid month format: {month}. Expected YYYY-MM")
        parsed = _parse_month(month)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    try:
        _usecase("reopen_labor_month_usecase").execute(
            ReopenLaborMonthRequest(project_id=UUID(project_id), month=parsed)
        )
    except MonthNotClosedError:
        return _error_response("NotFound", f"Month {month} is not closed", 404)
    return "", 204
