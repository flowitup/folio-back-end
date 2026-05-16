"""Project document API routes — list, upload, download, delete."""

from __future__ import annotations

from typing import Tuple
from uuid import UUID

import pydantic
from flask import Response, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.api.v1.project_documents import project_documents_bp
from app.api.v1.project_documents.schemas import ListQueryParams
from app.api.v1.projects.decorators import has_permission, require_permission, require_project_access
from app.api.v1.projects.schemas import ErrorResponse
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.application.project_documents import (
    DeleteProjectDocumentUseCase,  # noqa: F401 — referenced via container
    DocumentFileTooLargeError,
    DocumentPermissionDeniedError,
    EmptyFileError,
    ListFiltersDTO,
    ProjectDocumentNotFoundError,
    UnsupportedDocumentTypeError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def _serialize(doc) -> dict:
    return {
        "id": str(doc.id),
        "project_id": str(doc.project_id),
        "filename": doc.filename,
        "content_type": doc.content_type,
        "size_bytes": doc.size_bytes,
        "kind": doc.compute_kind(),
        "uploaded_at": doc.created_at.isoformat(),
        "uploader_id": str(doc.uploader_user_id),
        "download_url": f"/api/v1/projects/{doc.project_id}/documents/{doc.id}/download",
    }


@project_documents_bp.route("/projects/<project_id>/documents", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_project_documents(project_id: str):
    # Collect multi-valued ?type= params then flatten into a dict for Pydantic
    type_values = request.args.getlist("type")
    raw = {k: v for k, v in request.args.items() if k != "type"}
    if type_values:
        raw["type"] = type_values  # type: ignore[assignment]

    try:
        params = ListQueryParams.model_validate(raw)
    except pydantic.ValidationError as exc:
        return _error_response("INVALID_PARAMS", str(exc), 422)

    filters = ListFiltersDTO(
        kinds=tuple(params.type),
        uploader_id=params.uploader_id,
        sort=params.sort,
        order=params.order,
        page=params.page,
        per_page=params.per_page,
    )

    container = get_container()
    result = container.list_project_documents_usecase.execute(UUID(project_id), filters)

    return (
        jsonify(
            {
                "items": [_serialize(d) for d in result.items],
                "total": result.total,
                "page": params.page,
                "per_page": params.per_page,
            }
        ),
        200,
    )


@project_documents_bp.route("/projects/<project_id>/documents", methods=["POST"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
@limiter.limit("30 per minute", key_func=jwt_user_key)
def upload_project_document(project_id: str):
    if "file" not in request.files:
        return _error_response("MISSING_FILE", "No file part in request (expected field 'file')", 400)

    file = request.files["file"]
    if not file or file.filename == "":
        return _error_response("MISSING_FILE", "Empty filename", 400)

    # Determine size — Flask wraps the multipart stream; seek to end then back
    file.stream.seek(0, 2)
    size_bytes = file.stream.tell()
    file.stream.seek(0)

    uploader_id = UUID(get_jwt_identity())

    container = get_container()
    try:
        doc = container.upload_project_document_usecase.execute(
            project_id=UUID(project_id),
            filename=file.filename,
            content_type=file.mimetype or "application/octet-stream",
            size_bytes=size_bytes,
            fileobj=file.stream,
            uploader_user_id=uploader_id,
        )
    except EmptyFileError as exc:
        # 400, not 413 — an empty file is a bad request, not an oversize file (M3)
        return _error_response("EMPTY_FILE", str(exc), 400)
    except DocumentFileTooLargeError as exc:
        return _error_response("FILE_TOO_LARGE", str(exc), 413)
    except UnsupportedDocumentTypeError as exc:
        return _error_response("UNSUPPORTED_TYPE", str(exc), 415)

    return jsonify(_serialize(doc)), 201


@project_documents_bp.route("/projects/<project_id>/documents/<document_id>/download", methods=["GET"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def download_project_document(project_id: str, document_id: str):
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid document id", 400)

    container = get_container()
    try:
        # Pass expected_project_id so the use-case enforces the cross-project
        # invariant before opening the storage stream (H1: prevents S3 stream leak).
        doc, stream, length = container.get_project_document_usecase.execute(doc_uuid, UUID(project_id))
    except ProjectDocumentNotFoundError:
        return _error_response("NOT_FOUND", "Document not found", 404)

    # Inline preview only for PDF + images; everything else forced to download.
    # nosniff prevents browsers from MIME-sniffing user-controlled bytes into
    # a renderable type (defense against stored-XSS via spoofed mime).
    inline_safe = doc.content_type == "application/pdf" or doc.content_type.startswith("image/")
    response = send_file(
        stream,
        mimetype=doc.content_type,
        download_name=doc.filename,
        as_attachment=not inline_safe,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    if length:
        response.headers["Content-Length"] = str(length)
    return response


@project_documents_bp.route("/projects/<project_id>/documents/<document_id>", methods=["DELETE"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def delete_project_document(project_id: str, document_id: str):
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid document id", 400)

    container = get_container()

    # Re-load project entity — decorator validated access but use-case needs the entity
    project = container.project_repository.find_by_id(UUID(project_id))
    if project is None:
        return _error_response("NOT_FOUND", f"Project {project_id} not found", 404)

    requester_user_id = UUID(get_jwt_identity())
    is_admin = has_permission("*:*")

    try:
        container.delete_project_document_usecase.execute(
            doc_uuid,
            requester_user_id,
            project,
            is_admin=is_admin,
        )
    except DocumentPermissionDeniedError:
        return _error_response("FORBIDDEN", "You are not permitted to delete this document", 403)
    except ProjectDocumentNotFoundError:
        return _error_response("NOT_FOUND", "Document not found", 404)

    return "", 204
