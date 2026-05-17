"""Invoice API routes."""

import dataclasses
from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from pydantic import ValidationError

from app.api.v1.invoices import invoice_bp
from app.api.v1.invoices.schemas import CreateInvoiceSchema, UpdateInvoiceSchema
from app.api.v1.projects.decorators import (
    require_permission,
    require_project_access,
    require_invoice_access,
)
from app.api.v1.projects.schemas import ErrorResponse
from app.application.invoice import (
    CreateInvoiceRequest,
    ListInvoicesRequest,
    UpdateInvoiceRequest,
)
from app.domain.companies.exceptions import ForbiddenCompanyError
from app.domain.entities.invoice import InvoiceType
from app.domain.exceptions.invoice_exceptions import (
    InvalidInvoiceDataError,
    InvoiceNotFoundError,
    InvoiceNumberConflictError,
)
from app.domain.payment_methods.exceptions import PaymentMethodNotActiveError, PaymentMethodNotFoundError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    """Return a standardised JSON error response."""
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def _validation_error_response(e: ValidationError) -> Tuple[Response, int]:
    """Convert a Pydantic ValidationError to a 400 error response."""
    error_fields = [err.get("loc", ["unknown"])[-1] for err in e.errors()]
    return _error_response(
        "ValidationError",
        f"Invalid input: {', '.join(str(f) for f in error_fields)}",
        400,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_project_company_id(project_id: UUID) -> "UUID | None":
    """Return the company_id for a project, or None if not found / no company.

    Queries the ProjectModel directly so we can access company_id without
    extending the domain Project entity or IProjectRepository.
    """
    from app import db
    from app.infrastructure.database.models.project import ProjectModel

    row = db.session.get(ProjectModel, project_id)
    return row.company_id if row is not None else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@invoice_bp.route("/projects/<project_id>/invoices", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_invoices(project_id: str):
    """List invoices for a project, optionally filtered by ?type=."""
    invoice_type_param = request.args.get("type")
    try:
        parsed_type = InvoiceType(invoice_type_param) if invoice_type_param else None
    except ValueError:
        return _error_response(
            "ValidationError",
            f"Invalid type '{invoice_type_param}'. Must be one of: released_funds, labor, materials_services",
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

    return jsonify(
        {
            "invoices": [dataclasses.asdict(r) for r in results],
            "total": len(results),
        }
    )


@invoice_bp.route("/projects/<project_id>/invoices", methods=["POST"])
@jwt_required()
@limiter.limit("20 per minute")
@require_permission("project:manage_invoices")
@require_project_access(write=True)
def create_invoice(project_id: str):
    """Create a new invoice for a project."""
    try:
        data = CreateInvoiceSchema(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    jwt_claims = get_jwt()
    # JWT subject holds the authenticated user's UUID
    created_by = UUID(jwt_claims["sub"])

    project_uuid = UUID(project_id)
    company_id = _get_project_company_id(project_uuid)

    try:
        result = get_container().create_invoice_usecase.execute(
            CreateInvoiceRequest(
                project_id=project_uuid,
                created_by=created_by,
                type=InvoiceType(data.type),
                issue_date=data.issue_date,  # already a date object from Pydantic
                recipient_name=data.recipient_name,
                recipient_address=data.recipient_address,
                notes=data.notes,
                items=[item.model_dump() for item in data.items],
                payment_method_id=data.payment_method_id,
                company_id=company_id,
            )
        )
    except InvoiceNumberConflictError:
        return _error_response("Conflict", "Invoice number conflict, please retry", 409)
    except PaymentMethodNotFoundError:
        return _error_response("NotFound", "Payment method not found", 404)
    except PaymentMethodNotActiveError:
        return _error_response("Conflict", "Payment method is inactive and cannot be used", 409)
    except ForbiddenCompanyError:
        return _error_response("Forbidden", "Payment method belongs to a different company", 403)
    except ValueError as e:
        return _error_response("ValidationError", str(e), 400)
    except InvalidInvoiceDataError as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(dataclasses.asdict(result)), 201


@invoice_bp.route("/projects/<project_id>/invoices/<invoice_id>", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_invoice_access(write=False)
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
@require_invoice_access(write=True)
def update_invoice(project_id: str, invoice_id: str):
    """Partially update an invoice (type is immutable after creation)."""
    try:
        data = UpdateInvoiceSchema(**request.get_json())
    except ValidationError as e:
        return _validation_error_response(e)

    # Build kwargs — only pass fields the caller provided.
    # issue_date is already a date object from Pydantic (no manual conversion needed).
    # payment_method_id is handled separately: use exclude_unset so we can
    # distinguish "not provided" (absent) from "explicitly null".
    provided_fields = data.model_dump(exclude_unset=True)
    update_kwargs = {k: v for k, v in provided_fields.items() if k != "payment_method_id" and v is not None}

    # Verify invoice belongs to the requested project before updating
    try:
        existing = get_container().get_invoice_usecase.execute(UUID(invoice_id))
    except InvoiceNotFoundError:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)

    if existing.project_id != project_id:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)

    invoice_uuid = UUID(invoice_id)
    project_uuid = UUID(project_id)

    # Determine payment_method sentinel: _UNSET if not in request body, else the value.
    from app.application.invoice.update_invoice import _UNSET

    if "payment_method_id" in provided_fields:
        pm_id = provided_fields["payment_method_id"]  # UUID or None
        company_id = _get_project_company_id(project_uuid)
        update_req = UpdateInvoiceRequest(
            invoice_id=invoice_uuid,
            payment_method_id=pm_id,
            company_id=company_id,
            **update_kwargs,
        )
    else:
        update_req = UpdateInvoiceRequest(
            invoice_id=invoice_uuid,
            payment_method_id=_UNSET,
            **update_kwargs,
        )

    try:
        result = get_container().update_invoice_usecase.execute(update_req)
    except InvoiceNotFoundError:
        return _error_response("NotFound", f"Invoice {invoice_id} not found", 404)
    except PaymentMethodNotFoundError:
        return _error_response("NotFound", "Payment method not found", 404)
    except PaymentMethodNotActiveError:
        return _error_response("Conflict", "Payment method is inactive and cannot be used", 409)
    except ForbiddenCompanyError:
        return _error_response("Forbidden", "Payment method belongs to a different company", 403)
    except (ValueError, InvalidInvoiceDataError) as e:
        return _error_response("ValidationError", str(e), 400)

    return jsonify(dataclasses.asdict(result))


@invoice_bp.route("/projects/<project_id>/invoices/<invoice_id>", methods=["DELETE"])
@jwt_required()
@limiter.limit("20 per minute")
@require_permission("project:manage_invoices")
@require_invoice_access(write=True)
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
