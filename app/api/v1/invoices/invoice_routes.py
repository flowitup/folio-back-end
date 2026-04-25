"""Invoice API routes."""

import dataclasses
from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from pydantic import ValidationError

from app.api.v1.invoices import invoice_bp
from app.api.v1.invoices.schemas import CreateInvoiceSchema, UpdateInvoiceSchema
from app.api.v1.projects.decorators import require_permission
from app.api.v1.projects.schemas import ErrorResponse
from app.application.invoice import (
    CreateInvoiceRequest,
    ListInvoicesRequest,
    UpdateInvoiceRequest,
)
from app.domain.entities.invoice import InvoiceType
from app.domain.exceptions.invoice_exceptions import (
    InvalidInvoiceDataError,
    InvoiceNotFoundError,
    InvoiceNumberConflictError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    """Return a standardised JSON error response."""
    return jsonify(ErrorResponse(
        error=error, message=message, status_code=status_code
    ).model_dump()), status_code


def _validation_error_response(e: ValidationError) -> Tuple[Response, int]:
    """Convert a Pydantic ValidationError to a 400 error response."""
    error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
    return _error_response(
        "ValidationError",
        f"Invalid input: {', '.join(str(f) for f in error_fields)}",
        400,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@invoice_bp.route("/projects/<project_id>/invoices", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def list_invoices(project_id: str):
    """List invoices for a project, optionally filtered by ?type=."""
    invoice_type_param = request.args.get("type")
    try:
        parsed_type = InvoiceType(invoice_type_param) if invoice_type_param else None
    except ValueError:
        return _error_response(
            "ValidationError",
            f"Invalid type '{invoice_type_param}'. Must be one of: client, labor, supplier",
            400,
        )

    try:
        results = get_container().list_invoices_usecase.execute(
            ListInvoicesRequest(
                project_id=UUID(project_id),
                invoice_type=parsed_type,
            )
        )
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify({
        "invoices": [dataclasses.asdict(r) for r in results],
        "total": len(results),
    })


@invoice_bp.route("/projects/<project_id>/invoices", methods=["POST"])
@jwt_required()
@limiter.limit("20 per minute")
@require_permission("project:manage_invoices")
def create_invoice(project_id: str):
    """Create a new invoice for a project."""
    try:
        data = CreateInvoiceSchema(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    jwt_claims = get_jwt()
    # JWT subject holds the authenticated user's UUID
    created_by = UUID(jwt_claims["sub"])

    try:
        result = get_container().create_invoice_usecase.execute(
            CreateInvoiceRequest(
                project_id=UUID(project_id),
                created_by=created_by,
                type=InvoiceType(data.type),
                issue_date=data.issue_date,  # already a date object from Pydantic
                recipient_name=data.recipient_name,
                recipient_address=data.recipient_address,
                notes=data.notes,
                items=[item.model_dump() for item in data.items],
            )
        )
    except InvoiceNumberConflictError:
        return _error_response("Conflict", "Invoice number conflict, please retry", 409)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except InvalidInvoiceDataError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(dataclasses.asdict(result)), 201


@invoice_bp.route("/projects/<project_id>/invoices/<invoice_id>", methods=["GET"])
@jwt_required()
@require_permission("project:read")
def get_invoice(project_id: str, invoice_id: str):
    """Retrieve a single invoice by ID, scoped to the project."""
    try:
        result = get_container().get_invoice_usecase.execute(UUID(invoice_id))
    except InvoiceNotFoundError:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    # Verify invoice belongs to the requested project (prevents cross-project access)
    if result.project_id != project_id:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)

    return jsonify(dataclasses.asdict(result))


@invoice_bp.route("/projects/<project_id>/invoices/<invoice_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("20 per minute")
@require_permission("project:manage_invoices")
def update_invoice(project_id: str, invoice_id: str):
    """Partially update an invoice (type is immutable after creation)."""
    try:
        data = UpdateInvoiceSchema(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    # Build kwargs — only pass fields the caller provided
    # issue_date is already a date object from Pydantic (no manual conversion needed)
    update_kwargs = data.model_dump(exclude_none=True)

    # Verify invoice belongs to the requested project before updating
    try:
        existing = get_container().get_invoice_usecase.execute(UUID(invoice_id))
    except InvoiceNotFoundError:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)

    if existing.project_id != project_id:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)

    try:
        result = get_container().update_invoice_usecase.execute(
            UpdateInvoiceRequest(invoice_id=UUID(invoice_id), **update_kwargs)
        )
    except InvoiceNotFoundError:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)
    except (ValueError, InvalidInvoiceDataError) as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(dataclasses.asdict(result))


@invoice_bp.route("/projects/<project_id>/invoices/<invoice_id>", methods=["DELETE"])
@jwt_required()
@limiter.limit("20 per minute")
@require_permission("project:manage_invoices")
def delete_invoice(project_id: str, invoice_id: str):
    """Delete an invoice by ID, scoped to the project."""
    # Verify invoice belongs to the requested project before deleting
    try:
        existing = get_container().get_invoice_usecase.execute(UUID(invoice_id))
    except InvoiceNotFoundError:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)

    if existing.project_id != project_id:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)

    try:
        get_container().delete_invoice_usecase.execute(UUID(invoice_id))
    except InvoiceNotFoundError:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)

    return "", 204
