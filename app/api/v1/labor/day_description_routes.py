"""Labor day description API routes.

One free-text description per (project, date). Separate from the activity title
(labor_activities) and per-worker notes (labor_entries.note).

GET  /projects/<project_id>/labor-day-descriptions?from=YYYY-MM-DD&to=YYYY-MM-DD
     → 200 { "day_descriptions": [ {id, project_id, date, description, ...}, ... ] }

PUT  /projects/<project_id>/labor-day-descriptions  { "date": "YYYY-MM-DD", "description": str }
     → 200 full row object on upsert
     → 200 { "date": ..., "description": null, "deleted": true } when description blank (row cleared)
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import BaseModel, Field, ValidationError

from app.api.openapi import openapi_doc
from app.api.v1.labor import labor_bp
from app.api.v1.labor._labor_validation_error_helper import (
    _error_response,
    validation_error_response as _validation_error_response,
)
from app.api.v1.projects.decorators import require_permission
from app.application.labor.labor_day_description_usecases import (
    LaborDayDescriptionDetail,
    ListLaborDayDescriptionsRequest,
    SetLaborDayDescriptionRequest,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SetDayDescriptionSchema(BaseModel):
    """Request body for PUT /labor-day-descriptions.

    description may be empty/blank — the server will delete the row in that case.
    No min_length constraint here so callers can explicitly clear with "".
    """

    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    description: str = Field(..., max_length=2000)


class DayDescriptionResponse(BaseModel):
    id: str
    project_id: str
    date: str
    description: str
    created_by: Optional[str]
    created_at: str
    updated_at: str


class DayDescriptionListResponse(BaseModel):
    day_descriptions: list[DayDescriptionResponse]


class DayDescriptionDeletedResponse(BaseModel):
    """Returned when a blank description clears an existing row."""

    date: str
    description: None = None
    deleted: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")


def _detail_to_response(d: LaborDayDescriptionDetail) -> DayDescriptionResponse:
    return DayDescriptionResponse(
        id=str(d.id),
        project_id=str(d.project_id),
        date=d.date,
        description=d.description,
        created_by=d.created_by,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@labor_bp.route("/projects/<project_id>/labor-day-descriptions", methods=["GET"])
@openapi_doc(
    summary="List labor day descriptions for a project within a date range",
    tags=["labor"],
)
@jwt_required()
@require_permission("project:read")
def list_labor_day_descriptions(project_id: str):
    """List labor day descriptions for a project.

    Query params ``from`` and ``to`` (YYYY-MM-DD) are optional; when omitted the
    full history is returned (mirrors the labor-activities endpoint, so the
    attendance "all history" view can load without a month filter).
    """
    date_from_str = request.args.get("from")
    date_to_str = request.args.get("to")

    try:
        date_from = _parse_date(date_from_str) if date_from_str else None
        date_to = _parse_date(date_to_str) if date_to_str else None
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    descriptions = get_container().list_labor_day_descriptions_usecase.execute(
        ListLaborDayDescriptionsRequest(
            project_id=UUID(project_id),
            date_from=date_from,
            date_to=date_to,
        )
    )

    return jsonify(
        DayDescriptionListResponse(
            day_descriptions=[_detail_to_response(d) for d in descriptions],
        ).model_dump()
    )


@labor_bp.route("/projects/<project_id>/labor-day-descriptions", methods=["PUT"])
@openapi_doc(
    summary="Upsert (or clear) the labor day description for a project day",
    request=SetDayDescriptionSchema,
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute")
@require_permission("project:manage_labor")
def set_labor_day_description(project_id: str):
    """Upsert the day's single labor description.

    If description is blank/empty, the existing row is deleted and the response
    carries ``{ "date": ..., "description": null, "deleted": true }``.
    Otherwise the row is created or updated and the full row is returned.
    """
    try:
        data = SetDayDescriptionSchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        user_id = get_jwt_identity()
        description_date = _parse_date(data.date)
        result = get_container().set_labor_day_description_usecase.execute(
            SetLaborDayDescriptionRequest(
                project_id=UUID(project_id),
                date=description_date,
                description=data.description,
                created_by=UUID(user_id) if user_id else None,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    if result is None:
        # Blank description cleared the row
        return jsonify(DayDescriptionDeletedResponse(date=data.date).model_dump())

    return jsonify(_detail_to_response(result).model_dump())
