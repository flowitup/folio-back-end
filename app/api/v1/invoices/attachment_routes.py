"""Invoice attachment API routes — upload, list, download, delete."""

from __future__ import annotations

from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request, send_file
from flask_jwt_extended import get_jwt, jwt_required

from app.api.v1.invoices import invoice_bp
from app.api.v1.projects.decorators import (
    require_permission,
    require_invoice_access,
    require_attachment_access,
)
from app.api.v1.projects.schemas import ErrorResponse
from app.application.invoice import (
    AttachmentNotFoundError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.domain.exceptions.invoice_exceptions import InvoiceNotFoundError
from wiring import get_container


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def _serialize(att) -> dict:
    return {
        "id": str(att.id),
        "invoice_id": str(att.invoice_id),
        "filename": att.filename,
        "mime_type": att.mime_type,
        "size_bytes": att.size_bytes,
        "uploaded_at": att.uploaded_at.isoformat() if att.uploaded_at else None,
        "uploaded_by": str(att.uploaded_by) if att.uploaded_by else None,
        # Frontend builds the download URL from this id
        "download_url": f"/api/v1/attachments/{att.id}/download",
    }


@invoice_bp.route("/projects/<project_id>/invoices/<invoice_id>/attachments", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_invoice_access(write=False)
def list_attachments(project_id: str, invoice_id: str):
    try:
        inv_uuid = UUID(invoice_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid invoice id", 400)

    container = get_container()
    items = container.list_attachments_usecase.execute(inv_uuid)
    return jsonify([_serialize(a) for a in items]), 200


@invoice_bp.route("/projects/<project_id>/invoices/<invoice_id>/attachments", methods=["POST"])
@jwt_required()
@require_permission("project:manage_invoices")
@require_invoice_access(write=True)
def upload_attachment(project_id: str, invoice_id: str):
    try:
        inv_uuid = UUID(invoice_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid invoice id", 400)

    if "file" not in request.files:
        return _error_response("MISSING_FILE", "No file part in request (expected field 'file')", 400)

    file = request.files["file"]
    if not file or file.filename == "":
        return _error_response("MISSING_FILE", "Empty filename", 400)

    # Determine size — Flask wraps the multipart stream; seek to end then back
    file.stream.seek(0, 2)
    size_bytes = file.stream.tell()
    file.stream.seek(0)

    user_id_str = get_jwt().get("sub")
    user_id = UUID(user_id_str) if user_id_str else None

    container = get_container()
    try:
        att = container.upload_attachment_usecase.execute(
            invoice_id=inv_uuid,
            filename=file.filename,
            mime_type=file.mimetype or "application/octet-stream",
            size_bytes=size_bytes,
            fileobj=file.stream,
            uploaded_by=user_id,
        )
    except InvoiceNotFoundError as e:
        return _error_response("NOT_FOUND", str(e), 404)
    except FileTooLargeError as e:
        return _error_response("FILE_TOO_LARGE", str(e), 413)
    except UnsupportedFileTypeError as e:
        return _error_response("UNSUPPORTED_TYPE", str(e), 415)

    return jsonify(_serialize(att)), 201


@invoice_bp.route("/attachments/<attachment_id>/download", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_attachment_access(write=False)
def download_attachment(attachment_id: str):
    try:
        att_uuid = UUID(attachment_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid attachment id", 400)

    container = get_container()
    try:
        att, stream, length = container.get_attachment_usecase.execute(att_uuid)
    except AttachmentNotFoundError as e:
        return _error_response("NOT_FOUND", str(e), 404)

    # Inline preview only for PDF + images; everything else forced to download.
    # nosniff prevents browsers from MIME-sniffing user-controlled bytes into
    # a renderable type (defense against stored-XSS via spoofed mime).
    inline_safe = att.mime_type == "application/pdf" or att.mime_type.startswith("image/")
    response = send_file(
        stream,
        mimetype=att.mime_type,
        download_name=att.filename,
        as_attachment=not inline_safe,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    if length:
        response.headers["Content-Length"] = str(length)
    return response


@invoice_bp.route("/attachments/<attachment_id>", methods=["DELETE"])
@jwt_required()
@require_permission("project:manage_invoices")
@require_attachment_access(write=True)
def delete_attachment(attachment_id: str):
    try:
        att_uuid = UUID(attachment_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid attachment id", 400)

    container = get_container()
    try:
        container.delete_attachment_usecase.execute(att_uuid)
    except AttachmentNotFoundError as e:
        return _error_response("NOT_FOUND", str(e), 404)

    return "", 204
