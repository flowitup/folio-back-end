"""Labor payment note API routes.

One free-text note per (project, worker, month), shown on the worker's row of
the labor Payments tab (e.g. why a month is still unpaid).

GET  /projects/<project_id>/labor-payment-notes?month=YYYY-MM
     → 200 { "notes": [ {id, project_id, worker_id, month, note, ...}, ... ] }
     (``month`` optional; omitted → every note on the project)

PUT  /projects/<project_id>/labor-payment-notes  { "worker_id": uuid, "month": "YYYY-MM", "note": str }
     → 200 full note on upsert
     → 200 { "worker_id": ..., "month": ..., "note": null, "deleted": true } when the note is blank
     → 404 when the worker is not on the project
"""

from datetime import date, datetime
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
from app.application.labor.labor_payment_note_usecases import (
    LaborPaymentNoteDetail,
    LaborPaymentNoteWorkerNotFound,
    ListLaborPaymentNotesRequest,
    SetLaborPaymentNoteRequest,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class SetPaymentNoteSchema(BaseModel):
    """Request body for PUT /labor-payment-notes. A blank note clears the row."""

    worker_id: UUID
    month: str = Field(..., pattern=_MONTH_PATTERN)
    note: str = Field(..., max_length=2000)


class PaymentNoteResponse(BaseModel):
    id: str
    project_id: str
    worker_id: str
    month: str
    note: str
    created_by: Optional[str]
    created_at: str
    updated_at: str


class PaymentNoteListResponse(BaseModel):
    notes: list[PaymentNoteResponse]


class PaymentNoteDeletedResponse(BaseModel):
    """Returned when a blank note clears the existing row."""

    worker_id: str
    month: str
    note: None = None
    deleted: bool = True


def _parse_month(month_str: str) -> date:
    try:
        return datetime.strptime(month_str, "%Y-%m").date()
    except ValueError:
        raise ValueError(f"Invalid month format: {month_str}. Expected YYYY-MM")


def _detail_to_response(d: LaborPaymentNoteDetail) -> PaymentNoteResponse:
    return PaymentNoteResponse(
        id=str(d.id),
        project_id=str(d.project_id),
        worker_id=str(d.worker_id),
        month=d.month,
        note=d.note,
        created_by=d.created_by,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@labor_bp.route("/projects/<project_id>/labor-payment-notes", methods=["GET"])
@openapi_doc(
    summary="List labor payment notes for a project, optionally for one month",
    tags=["labor"],
)
@jwt_required()
@require_permission("project:read")
def list_labor_payment_notes(project_id: str):
    month_str = request.args.get("month")
    try:
        project_uuid = UUID(project_id)
        month = _parse_month(month_str) if month_str else None
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    notes = get_container().list_labor_payment_notes_usecase.execute(
        ListLaborPaymentNotesRequest(project_id=project_uuid, month=month)
    )
    return jsonify(PaymentNoteListResponse(notes=[_detail_to_response(n) for n in notes]).model_dump())


@labor_bp.route("/projects/<project_id>/labor-payment-notes", methods=["PUT"])
@openapi_doc(
    summary="Upsert (or clear) the note on a worker's labor charges for a month",
    request=SetPaymentNoteSchema,
    tags=["labor"],
)
@jwt_required()
@limiter.limit("30 per minute")
# Same permission as the rest of the Payments tab's writes (record payment,
# quick-assign), so whoever can settle a month can also explain it.
@require_permission("project:manage_invoices")
def set_labor_payment_note(project_id: str):
    try:
        data = SetPaymentNoteSchema(**(request.get_json() or {}))
    except ValidationError as e:
        return _validation_error_response(e)

    try:
        user_id = get_jwt_identity()
        result = get_container().set_labor_payment_note_usecase.execute(
            SetLaborPaymentNoteRequest(
                project_id=UUID(project_id),
                worker_id=data.worker_id,
                month=_parse_month(data.month),
                note=data.note,
                created_by=UUID(user_id) if user_id else None,
            )
        )
    except LaborPaymentNoteWorkerNotFound:
        return _error_response("NotFound", "Worker not found on this project", 404)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    if result is None:
        return jsonify(PaymentNoteDeletedResponse(worker_id=str(data.worker_id), month=data.month).model_dump())
    return jsonify(_detail_to_response(result).model_dump())
