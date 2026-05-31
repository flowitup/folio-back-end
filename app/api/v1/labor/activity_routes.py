"""Labor activity API routes."""

from datetime import datetime
from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from pydantic import BaseModel, Field, ValidationError
from typing import Optional, List

from app.api.openapi import openapi_doc
from app.api.v1.labor import labor_bp
from app.api.v1.labor._labor_validation_error_helper import (
    _error_response,
    validation_error_response as _validation_error_response,
)
from app.api.v1.projects.decorators import require_permission
from app.application.labor.labor_activity_usecases import (
    CreateLaborActivityRequest,
    ListLaborActivitiesRequest,
    UpdateLaborActivityRequest,
    DeleteLaborActivityRequest,
    LaborActivityDetail,
)
from app.domain.exceptions.labor_exceptions import LaborActivityNotFoundError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateActivitySchema(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)


class UpdateActivitySchema(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)


class ActivityResponse(BaseModel):
    id: str
    project_id: str
    date: str
    title: str
    description: Optional[str]
    created_by: Optional[str]
    created_at: str
    updated_at: str


class ActivityListResponse(BaseModel):
    activities: List[ActivityResponse]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")


def _detail_to_response(d: LaborActivityDetail) -> ActivityResponse:
    return ActivityResponse(
        id=str(d.id),
        project_id=str(d.project_id),
        date=d.date,
        title=d.title,
        description=d.description,
        created_by=d.created_by,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@labor_bp.route("/projects/<project_id>/labor-activities", methods=["GET"])
@openapi_doc(summary="List labor activities for a project with optional date filters", tags=["labor"])
@jwt_required()
@require_permission("project:read")
def list_labor_activities(project_id: str):
    """List labor activities for a project with optional date filters."""
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    try:
        activities = get_container().list_labor_activities_usecase.execute(
            ListLaborActivitiesRequest(
                project_id=UUID(project_id),
                date_from=_parse_date(date_from) if date_from else None,
                date_to=_parse_date(date_to) if date_to else None,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(
        ActivityListResponse(
            activities=[_detail_to_response(a) for a in activities],
            total=len(activities),
        ).model_dump()
    )


@labor_bp.route("/projects/<project_id>/labor-activities", methods=["POST"])
@openapi_doc(
    summary="Create a new labor activity for a project day",
    request=CreateActivitySchema,
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute")
@require_permission("project:manage_labor")
def create_labor_activity(project_id: str):
    """Create a new labor activity for a project day."""
    try:
        data = CreateActivitySchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        user_id = get_jwt_identity()
        result = get_container().create_labor_activity_usecase.execute(
            CreateLaborActivityRequest(
                project_id=UUID(project_id),
                date=_parse_date(data.date),
                title=data.title,
                description=data.description,
                created_by=UUID(user_id) if user_id else None,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(_detail_to_response(result).model_dump()), 201


@labor_bp.route("/projects/<project_id>/labor-activities/<activity_id>", methods=["PUT"])
@openapi_doc(
    summary="Update an existing labor activity",
    request=UpdateActivitySchema,
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute")
@require_permission("project:manage_labor")
def update_labor_activity(project_id: str, activity_id: str):
    """Update an existing labor activity."""
    try:
        data = UpdateActivitySchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        result = get_container().update_labor_activity_usecase.execute(
            UpdateLaborActivityRequest(
                activity_id=UUID(activity_id),
                title=data.title,
                description=data.description,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except LaborActivityNotFoundError:
        return _error_response("NotFound", f"Labor activity {activity_id} not found", 404)

    return jsonify(_detail_to_response(result).model_dump())


@labor_bp.route("/projects/<project_id>/labor-activities/<activity_id>", methods=["DELETE"])
@openapi_doc(summary="Delete a labor activity", tags=["labor"])
@jwt_required()
@limiter.limit("30 per minute")
@require_permission("project:manage_labor")
def delete_labor_activity(project_id: str, activity_id: str):
    """Delete a labor activity."""
    try:
        get_container().delete_labor_activity_usecase.execute(DeleteLaborActivityRequest(activity_id=UUID(activity_id)))
    except LaborActivityNotFoundError:
        return _error_response("NotFound", f"Labor activity {activity_id} not found", 404)

    return "", 204
