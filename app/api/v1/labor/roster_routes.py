"""Day roster route (D3): name, presence, hours, day type — never money.

See `app.application.labor.get_day_roster_usecase` for why this endpoint
answers 404 to any caller who is not assigned to the project, a company
admin of its owning company, or a platform `*:*` holder, instead of the
usual 403 the rest of the project-scoped API returns for a related-but-
unauthorized caller — the roster's very existence must not leak to someone
who has no relationship to the project at all.
"""

from __future__ import annotations

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api.openapi import openapi_doc
from app.api.v1.labor import labor_bp
from app.api.v1.labor._labor_validation_error_helper import _error_response
from app.api.v1.labor._labor_validation_error_helper import parse_iso_date as _parse_date
from app.api.v1.labor.roster_schemas import RosterQuery, RosterResponse, RosterRowResponse
from app.api.v1.projects.decorators import _is_platform_admin
from app.application.labor.get_day_roster_usecase import GetDayRosterRequest
from wiring import get_container


@labor_bp.route("/projects/<project_id>/labor/roster", methods=["GET"])
@openapi_doc(
    summary="Day roster for a project: name, presence, hours, day type (never pay)",
    query=RosterQuery,
    responses={200: RosterResponse},
    tags=["labor"],
)
@jwt_required()
def get_day_roster(project_id: str):
    """Return the day roster, or 404 when the caller has no relation to the project.

    Deliberately NOT decorated with `require_project_access` / `require_permission`
    — both answer 403 for an unrelated caller, which confirms the project exists.
    The tenant check (assigned OR company admin OR platform `*:*`) lives entirely
    in `GetDayRosterUseCase`, evaluated against `AuthzReaderPort` only (never the
    caller's raw JWT `permissions` claim), so a stale/legacy global-role claim
    cannot substitute for an actual company relationship.
    """
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return _error_response("NotFound", f"Project {project_id} not found", 404)

    try:
        query = RosterQuery(date=request.args.get("date", ""))
    except ValidationError:
        return _error_response("ValidationError", "Missing or invalid ?date=YYYY-MM-DD", 400)

    try:
        target_date = _parse_date(query.date)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    usecase = get_container().get_day_roster_usecase
    if usecase is None:
        raise RuntimeError("get_day_roster_usecase not wired in container")

    rows = usecase.execute(
        GetDayRosterRequest(
            project_id=project_uuid,
            date=target_date,
            caller_user_id=UUID(str(get_jwt_identity())),
            is_platform_admin=_is_platform_admin(),
        )
    )
    if rows is None:
        return _error_response("NotFound", f"Project {project_id} not found", 404)

    return jsonify(
        RosterResponse(
            rows=[
                RosterRowResponse(
                    worker_id=str(r.worker_id),
                    name=r.name,
                    status=r.status,
                    hours=r.hours,
                    day_type=r.day_type,
                )
                for r in rows
            ]
        ).model_dump()
    )
