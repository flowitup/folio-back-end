"""Invoice export route — monthly batch xlsx/pdf."""

from __future__ import annotations

from io import BytesIO
from uuid import UUID

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import jwt_required
from pydantic import ValidationError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api._helpers.requester_identity import get_requester_email
from app.api.v1.invoices.schemas import ExportInvoicesQuery
from app.api.v1.projects.decorators import require_permission, require_project_access
from app.application.invoice.export_invoices_usecase import ExportInvoicesRequest
from app.domain.entities.invoice import InvoiceType
from app.domain.exceptions.project_exceptions import ProjectNotFoundError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

invoice_export_bp = Blueprint("invoice_export", __name__)


@invoice_export_bp.route("/projects/<project_id>/invoices-export", methods=["GET"])
@jwt_required()
@limiter.limit("5 per minute", key_func=jwt_user_key)
@require_permission("project:read")
@require_project_access()
def export_invoices(project_id: str):
    """Stream xlsx or pdf export for a project's invoices.

    Query params:
        from   (str, YYYY-MM) — start month, inclusive
        to     (str, YYYY-MM) — end month, inclusive
        format (str)          — "xlsx" or "pdf"
        type   (str, optional) — "released_funds", "labor", or "supplier"

    Returns:
        200: binary file stream with Content-Disposition: attachment
        404: project not found
        422: Pydantic validation error (from/to format, range span, invalid format)
    """
    # --- Validate query string via Pydantic ---
    try:
        query = ExportInvoicesQuery.model_validate(request.args.to_dict())
    except ValidationError as exc:
        return format_validation_error(exc)

    # --- Resolve acting user email (needed for file metadata) ---
    container = get_container()
    requester_email = get_requester_email(container.user_repository)

    # --- Execute use-case ---
    try:
        result = container.export_invoices_usecase.execute(
            ExportInvoicesRequest(
                project_id=UUID(project_id),
                from_month=query.from_month,
                to_month=query.to_month,
                format=query.format,
                acting_user_email=requester_email,
                type_filter=InvoiceType(query.type) if query.type else None,
            )
        )
    except ProjectNotFoundError:
        return jsonify({"error": "project_not_found", "message": f"Project {project_id} not found"}), 404

    # --- Stream file with security headers ---
    response = send_file(
        BytesIO(result.content),
        mimetype=result.mime_type,
        download_name=result.filename,
        as_attachment=True,
    )
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
