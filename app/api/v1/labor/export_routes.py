"""Labor export routes — project-wide and per-worker variants."""

from __future__ import annotations

from io import BytesIO
from uuid import UUID

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api.v1.projects.decorators import require_permission
from app.application.labor.export_labor_usecase import ExportLaborRequest
from app.api.v1.labor.schemas import ExportLaborQuery
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError
from app.domain.exceptions.project_exceptions import ProjectNotFoundError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

labor_export_bp = Blueprint("labor_export", __name__)


@labor_export_bp.route("/projects/<project_id>/labor-export", methods=["GET"])
@jwt_required()
@limiter.limit("5 per minute")
@require_permission("project:read")
def export_labor(project_id: str):
    """Stream xlsx or pdf export for a project's labor data.

    Query params:
        from   (str, YYYY-MM) — start month, inclusive
        to     (str, YYYY-MM) — end month, inclusive
        format (str)          — "xlsx" or "pdf"

    Returns:
        200: binary file stream with Content-Disposition: attachment
        400: Pydantic validation error (from/to format, range span)
        403: caller lacks project:read (handled by @require_permission)
        404: project not found
        500: unexpected generation error (logged by Flask)
    """
    # --- Validate query string via Pydantic ---
    try:
        query = ExportLaborQuery.model_validate(request.args.to_dict())
    except ValidationError as exc:
        # Build a JSON-safe error list — exc.errors() may embed ValueError objects
        # in the 'ctx' field when model_validators raise, which Flask's jsonify
        # cannot serialise.
        safe_errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        detail = "; ".join(f"{('.'.join(str(loc) for loc in e['loc']) or 'value')}: {e['msg']}" for e in safe_errors)
        return jsonify({"error": "validation_error", "details": safe_errors, "message": detail}), 422

    # --- Resolve acting user email (needed for file metadata) ---
    container = get_container()
    requester_id = UUID(get_jwt_identity())
    user = container.user_repository.find_by_id(requester_id)
    requester_email = user.email if user else "unknown"

    # --- Execute use-case ---
    try:
        result = container.export_labor_usecase.execute(
            ExportLaborRequest(
                project_id=UUID(project_id),
                from_month=query.from_month,
                to_month=query.to_month,
                format=query.format,
                acting_user_email=requester_email,
            )
        )
    except ProjectNotFoundError:
        return jsonify({"error": "project_not_found", "message": f"Project {project_id} not found"}), 404

    # --- Stream file with security headers (mirrors attachment_routes.py:125-135) ---
    response = send_file(
        BytesIO(result.content),
        mimetype=result.mime_type,
        download_name=result.filename,
        as_attachment=True,
    )
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@labor_export_bp.route(
    "/projects/<project_id>/workers/<worker_id>/labor-export",
    methods=["GET"],
)
@jwt_required()
@limiter.limit("5 per minute")
@require_permission("project:read")
def export_worker_labor(project_id: str, worker_id: str):
    """Stream xlsx or pdf export for a single worker's labor data.

    Query params:
        from   (str, YYYY-MM) — start month, inclusive
        to     (str, YYYY-MM) — end month, inclusive
        format (str)          — "xlsx" or "pdf"

    Returns:
        200: binary file stream with Content-Disposition: attachment
        404: project not found or worker not found / not in project
        422: invalid worker_id UUID or Pydantic validation error
        429: rate limit exceeded (5 per minute)
    """
    # --- Validate worker_id path param ---
    try:
        worker_uuid = UUID(worker_id)
    except ValueError:
        return jsonify({"error": "invalid_worker_id", "message": f"Invalid worker id: {worker_id}"}), 422

    # --- Validate query string via Pydantic ---
    try:
        query = ExportLaborQuery.model_validate(request.args.to_dict())
    except ValidationError as exc:
        safe_errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        detail = "; ".join(f"{('.'.join(str(loc) for loc in e['loc']) or 'value')}: {e['msg']}" for e in safe_errors)
        return jsonify({"error": "validation_error", "details": safe_errors, "message": detail}), 422

    # --- Resolve acting user email ---
    container = get_container()
    requester_id = UUID(get_jwt_identity())
    user = container.user_repository.find_by_id(requester_id)
    requester_email = user.email if user else "unknown"

    # --- Execute use-case ---
    try:
        result = container.export_labor_usecase.execute(
            ExportLaborRequest(
                project_id=UUID(project_id),
                worker_id=worker_uuid,
                from_month=query.from_month,
                to_month=query.to_month,
                format=query.format,
                acting_user_email=requester_email,
            )
        )
    except ProjectNotFoundError:
        return jsonify({"error": "project_not_found", "message": f"Project {project_id} not found"}), 404
    except WorkerNotFoundError:
        return jsonify({"error": "worker_not_found", "message": f"Worker {worker_id} not found"}), 404

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
