"""Project document API routes — list, upload, download, delete."""

from __future__ import annotations

from typing import Tuple
from uuid import UUID

import pydantic
from flask import Response, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.api.openapi import openapi_doc
from app.api.v1.project_documents import project_documents_bp
from app.api.v1.project_documents.schemas import ListQueryParams
from app.api.v1.projects.decorators import has_permission, require_permission, require_project_access
from app.api.v1.projects.schemas import ErrorResponse
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.application.project_documents import (
    DocumentFileTooLargeError,
    DocumentPermissionDeniedError,
    EmptyFileError,
    ListFiltersDTO,
    ProjectDocumentNotFoundError,
    UnsupportedDocumentTypeError,
)
from app.application.project_documents.confirm_project_document_upload import (
    DocumentNotInStorageError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def _serialize(doc) -> dict:
    """Serialize a ProjectDocument to the API response shape.

    `download_url` is a **relative** path (no scheme/host). Clients must prefix
    it with the BE base URL (e.g. NEXT_PUBLIC_API_BASE_URL on the FE). This
    avoids baking environment-specific hostnames into responses and keeps the
    payload usable across docker compose, dev, staging, and prod.
    """
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
        "tags": list(doc.tags),
    }


@project_documents_bp.route("/projects/<project_id>/documents", methods=["GET"])
@openapi_doc(
    summary="List project documents with optional filters",
    query=ListQueryParams,
    tags=["project_documents"],
)
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_project_documents(project_id: str):
    # Collect multi-valued ?type= and ?tag= params then flatten into a dict for Pydantic
    type_values = request.args.getlist("type")
    tag_values = request.args.getlist("tag")
    raw = {k: v for k, v in request.args.items() if k not in ("type", "tag")}
    if type_values:
        raw["type"] = type_values  # type: ignore[assignment]
    if tag_values:
        raw["tag"] = tag_values  # type: ignore[assignment]

    try:
        params = ListQueryParams.model_validate(raw)
    except pydantic.ValidationError as exc:
        return _error_response("INVALID_PARAMS", str(exc), 422)

    filters = ListFiltersDTO(
        kinds=tuple(params.type),
        tags=tuple(params.tag),
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
@openapi_doc(summary="Upload a document to a project (multipart/form-data)", tags=["project_documents"])
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


@project_documents_bp.route("/projects/<project_id>/documents/presign", methods=["POST"])
@openapi_doc(summary="Generate a presigned PUT URL for direct-to-S3 browser upload", tags=["project_documents"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
@limiter.limit("30 per minute", key_func=jwt_user_key)
def presign_project_document(project_id: str):
    """Generate a presigned PUT URL for direct-to-S3 browser upload."""
    body = request.get_json(silent=True)
    if not body:
        return _error_response("INVALID_BODY", "Request body must be JSON with filename, content_type, size_bytes", 400)

    filename = body.get("filename")
    content_type = body.get("content_type", "application/octet-stream")
    size_bytes = body.get("size_bytes")

    if not filename or not isinstance(filename, str):
        return _error_response("MISSING_FILENAME", "filename is required", 400)
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        return _error_response("INVALID_SIZE", "size_bytes must be a positive integer", 400)

    container = get_container()

    if container.presign_project_document_usecase is None:
        return _error_response(
            "NOT_AVAILABLE",
            "Presigned uploads are not configured on this server",
            501,
        )

    try:
        result = container.presign_project_document_usecase.execute(
            project_id=UUID(project_id),
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
        )
    except EmptyFileError as exc:
        return _error_response("EMPTY_FILE", str(exc), 400)
    except DocumentFileTooLargeError as exc:
        return _error_response("FILE_TOO_LARGE", str(exc), 413)
    except UnsupportedDocumentTypeError as exc:
        return _error_response("UNSUPPORTED_TYPE", str(exc), 415)
    except RuntimeError as exc:
        # generate_presigned_put_url raises RuntimeError when public endpoint missing
        return _error_response("NOT_AVAILABLE", str(exc), 501)

    return (
        jsonify(
            {
                "presigned_url": result.presigned_url,
                "storage_key": result.storage_key,
                "doc_id": result.doc_id,
            }
        ),
        200,
    )


@project_documents_bp.route("/projects/<project_id>/documents/confirm", methods=["POST"])
@openapi_doc(
    summary="Confirm a presigned upload — verify S3 object exists and persist DB row", tags=["project_documents"]
)
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
@limiter.limit("30 per minute", key_func=jwt_user_key)
def confirm_project_document_upload(project_id: str):
    """Confirm a presigned upload — verify S3 object exists and persist DB row."""
    body = request.get_json(silent=True)
    if not body:
        return _error_response("INVALID_BODY", "Request body must be JSON", 400)

    doc_id_str = body.get("doc_id")
    storage_key = body.get("storage_key")
    filename = body.get("filename")
    content_type = body.get("content_type", "application/octet-stream")
    size_bytes = body.get("size_bytes")

    # Validate required fields
    if not doc_id_str or not isinstance(doc_id_str, str):
        return _error_response("MISSING_DOC_ID", "doc_id is required", 400)
    if not storage_key or not isinstance(storage_key, str):
        return _error_response("MISSING_STORAGE_KEY", "storage_key is required", 400)
    if not filename or not isinstance(filename, str):
        return _error_response("MISSING_FILENAME", "filename is required", 400)
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        return _error_response("INVALID_SIZE", "size_bytes must be a positive integer", 400)

    # Validate UUID format
    try:
        doc_uuid = UUID(doc_id_str)
    except ValueError:
        return _error_response("INVALID_DOC_ID", "doc_id must be a valid UUID", 400)

    # Security: verify storage_key contains this project_id to prevent
    # cross-project confirmation attacks
    expected_prefix = f"project-documents/{project_id}/"
    if not storage_key.startswith(expected_prefix):
        return _error_response(
            "KEY_PROJECT_MISMATCH",
            "storage_key does not belong to this project",
            400,
        )

    uploader_id = UUID(get_jwt_identity())
    container = get_container()

    if container.confirm_project_document_usecase is None:
        return _error_response("NOT_AVAILABLE", "Presigned uploads are not configured", 501)

    try:
        doc = container.confirm_project_document_usecase.execute(
            project_id=UUID(project_id),
            doc_id=doc_uuid,
            storage_key=storage_key,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            uploader_user_id=uploader_id,
        )
    except DocumentNotInStorageError:
        return _error_response(
            "OBJECT_NOT_FOUND",
            "File not found in storage — upload may have failed or expired",
            404,
        )

    return jsonify(_serialize(doc)), 201


@project_documents_bp.route("/projects/<project_id>/documents/<document_id>/preview-url", methods=["GET"])
@openapi_doc(summary="Return a short-lived presigned GET URL for browser preview", tags=["project_documents"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def get_document_preview_url(project_id: str, document_id: str):
    """Return a short-lived presigned GET URL so the browser can load the
    document directly from S3/MinIO — bypasses Flask streaming entirely."""
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid document id", 400)

    container = get_container()
    doc = container.project_document_repository.find_by_id(doc_uuid)
    if doc is None or doc.deleted_at is not None:
        return _error_response("NOT_FOUND", "Document not found", 404)

    # Cross-project guard
    if str(doc.project_id) != project_id:
        return _error_response("NOT_FOUND", "Document not found", 404)

    # Only serve presigned URLs for inline-safe types (PDF + images)
    inline_safe = doc.content_type == "application/pdf" or doc.content_type.startswith("image/")
    if not inline_safe:
        return _error_response("NOT_PREVIEWABLE", "Document type does not support preview", 400)

    storage = container.document_storage
    if not storage.presigned_uploads_enabled:
        return _error_response("NOT_AVAILABLE", "Presigned URLs not configured", 501)

    try:
        url = storage.generate_presigned_get_url(doc.storage_key, expires_in=3600)
    except RuntimeError as exc:
        return _error_response("NOT_AVAILABLE", str(exc), 501)

    return jsonify({"url": url, "content_type": doc.content_type, "filename": doc.filename}), 200


@project_documents_bp.route("/projects/<project_id>/documents/<document_id>/download", methods=["GET"])
@openapi_doc(summary="Download a project document", tags=["project_documents"])
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
    # Cache immutable document bytes in browser for 1 hour; S3 objects are write-once
    response.headers["Cache-Control"] = "private, max-age=3600, immutable"
    if length:
        response.headers["Content-Length"] = str(length)
    return response


@project_documents_bp.route("/projects/<project_id>/documents/<document_id>/rename", methods=["PATCH"])
@openapi_doc(summary="Rename a project document", tags=["project_documents"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def rename_project_document(project_id: str, document_id: str):
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid document id", 400)

    body = request.get_json(silent=True)
    if not body or "filename" not in body:
        return _error_response("MISSING_FILENAME", "Request body must include 'filename'", 400)

    new_filename = body["filename"]
    if not isinstance(new_filename, str) or not new_filename.strip():
        return _error_response("INVALID_FILENAME", "Filename must be a non-empty string", 400)

    container = get_container()

    project = container.project_repository.find_by_id(UUID(project_id))
    if project is None:
        return _error_response("NOT_FOUND", f"Project {project_id} not found", 404)

    requester_user_id = UUID(get_jwt_identity())
    is_admin = has_permission("*:*")

    try:
        doc = container.rename_project_document_usecase.execute(
            doc_uuid,
            new_filename.strip(),
            requester_user_id,
            project,
            is_admin=is_admin,
        )
    except DocumentPermissionDeniedError:
        return _error_response("FORBIDDEN", "You are not permitted to rename this document", 403)
    except ProjectDocumentNotFoundError:
        return _error_response("NOT_FOUND", "Document not found", 404)
    except ValueError as exc:
        return _error_response("INVALID_FILENAME", str(exc), 400)

    return jsonify(_serialize(doc)), 200


@project_documents_bp.route("/projects/<project_id>/documents/<document_id>", methods=["DELETE"])
@openapi_doc(summary="Delete a project document", tags=["project_documents"])
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


@project_documents_bp.route("/projects/<project_id>/documents/<document_id>/tags", methods=["PUT"])
@openapi_doc(summary="Replace all tags on a project document", tags=["project_documents"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def update_document_tags(project_id: str, document_id: str):
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid document id", 400)

    body = request.get_json(silent=True)
    if not body or "tags" not in body:
        return _error_response("MISSING_TAGS", "Request body must include 'tags'", 400)

    raw_tags = body["tags"]
    if not isinstance(raw_tags, list) or not all(isinstance(t, str) for t in raw_tags):
        return _error_response("INVALID_TAGS", "tags must be an array of strings", 400)

    tags = [t.strip().lower() for t in raw_tags if t.strip()]
    if len(tags) > 20:
        return _error_response("TOO_MANY_TAGS", "Maximum 20 tags per document", 400)
    for tag in tags:
        if len(tag) > 100:
            return _error_response("TAG_TOO_LONG", "Each tag must be 100 characters or fewer", 400)

    tags = list(dict.fromkeys(tags))

    container = get_container()
    doc = container.project_document_repository.find_by_id(doc_uuid)
    if doc is None or doc.deleted_at is not None or str(doc.project_id) != project_id:
        return _error_response("NOT_FOUND", "Document not found", 404)

    container.project_document_repository.set_tags(doc_uuid, tags)

    from app import db as _db

    _db.session.commit()

    updated = container.project_document_repository.find_by_id(doc_uuid)
    return jsonify(_serialize(updated)), 200


@project_documents_bp.route("/projects/<project_id>/documents/tags", methods=["GET"])
@openapi_doc(summary="List all tags used in a project's documents", tags=["project_documents"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_project_document_tags(project_id: str):
    container = get_container()
    tags = container.project_document_repository.list_tags_for_project(UUID(project_id))
    return jsonify({"tags": tags}), 200
