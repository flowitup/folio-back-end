"""Project photo API routes — upload, list, stream, update, delete."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Tuple
from uuid import UUID

import pydantic
from flask import Response, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.api.openapi import openapi_doc
from app.api.v1.project_photos import project_photos_bp
from app.api.v1.project_photos.schemas import ListQueryParams, UpdatePhotoBody
from app.api.v1.projects.decorators import has_permission, require_permission, require_project_access
from app.api.v1.projects.schemas import ErrorResponse
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.application.project_photos import (
    EmptyImageError,
    ImageTooLargeError,
    PhotoNotFoundError,
    PhotoPermissionDeniedError,
    ThumbnailGenerationError,
    UnsupportedImageTypeError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def _serialize(photo) -> dict:
    """Serialize a ProjectPhoto entity to the API response shape.

    URLs are relative paths (no scheme/host) so the payload is usable across
    dev, staging, and prod without baking in environment-specific hostnames.
    """
    pid = str(photo.project_id)
    uid = str(photo.id)
    return {
        "id": uid,
        "project_id": pid,
        "filename": photo.filename,
        "content_type": photo.content_type,
        "size_bytes": photo.size_bytes,
        "caption": photo.caption,
        "captured_at": photo.captured_at.isoformat(),
        "uploaded_at": photo.created_at.isoformat(),
        "uploader_id": str(photo.uploader_user_id),
        "thumbnail_url": f"/api/v1/projects/{pid}/photos/{uid}/thumbnail",
        "original_url": f"/api/v1/projects/{pid}/photos/{uid}/original",
    }


def _parse_captured_at(raw: str | None) -> datetime | None:
    """Parse captured_at from ISO datetime or YYYY-MM-DD date string.

    A date-only value is normalized to UTC midnight so the DB always stores a
    tz-aware timestamp.

    Returns None if raw is None. Raises ValueError on invalid format.
    """
    if raw is None:
        return None
    raw = raw.strip()
    # Try full ISO datetime first
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # Try date-only YYYY-MM-DD
    try:
        d = date.fromisoformat(raw)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"Invalid captured_at value '{raw}' — expected ISO datetime or YYYY-MM-DD")


@project_photos_bp.route("/projects/<project_id>/photos", methods=["POST"])
@openapi_doc(summary="Upload a progress photo to a project (multipart/form-data)", tags=["project_photos"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
@limiter.limit("30 per minute", key_func=jwt_user_key)
def upload_project_photo(project_id: str):
    if "file" not in request.files:
        return _error_response("MISSING_FILE", "No file part in request (expected field 'file')", 400)

    file = request.files["file"]
    if not file or file.filename == "":
        return _error_response("MISSING_FILE", "Empty filename", 400)

    data = file.stream.read()
    size_bytes = len(data)

    caption = request.form.get("caption") or None
    raw_captured_at = request.form.get("captured_at")
    try:
        captured_at = _parse_captured_at(raw_captured_at)
    except ValueError as exc:
        return _error_response("INVALID_CAPTURED_AT", str(exc), 422)

    uploader_id = UUID(get_jwt_identity())
    container = get_container()

    try:
        photo = container.upload_project_photo_usecase.execute(
            project_id=UUID(project_id),
            filename=file.filename,
            content_type=file.mimetype or "application/octet-stream",
            size_bytes=size_bytes,
            data=data,
            uploader_user_id=uploader_id,
            caption=caption,
            captured_at=captured_at,
        )
    except EmptyImageError as exc:
        return _error_response("EMPTY_FILE", str(exc), 400)
    except ImageTooLargeError as exc:
        return _error_response("FILE_TOO_LARGE", str(exc), 413)
    except UnsupportedImageTypeError as exc:
        return _error_response("UNSUPPORTED_TYPE", str(exc), 415)
    except ThumbnailGenerationError as exc:
        return _error_response("INVALID_IMAGE", str(exc), 422)

    return jsonify(_serialize(photo)), 201


@project_photos_bp.route("/projects/<project_id>/photos", methods=["GET"])
@openapi_doc(
    summary="List progress photos for a project",
    query=ListQueryParams,
    tags=["project_photos"],
)
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def list_project_photos(project_id: str):
    try:
        params = ListQueryParams.model_validate(request.args.to_dict())
    except pydantic.ValidationError as exc:
        return _error_response("INVALID_PARAMS", str(exc), 422)

    container = get_container()
    result = container.list_project_photos_usecase.execute(UUID(project_id), params.page, params.per_page)

    return (
        jsonify(
            {
                "items": [_serialize(p) for p in result.items],
                "total": result.total,
                "page": params.page,
                "per_page": params.per_page,
            }
        ),
        200,
    )


@project_photos_bp.route("/projects/<project_id>/photos/<photo_id>/thumbnail", methods=["GET"])
@openapi_doc(summary="Stream the JPEG thumbnail for a project photo", tags=["project_photos"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def get_photo_thumbnail(project_id: str, photo_id: str):
    try:
        pid_uuid = UUID(photo_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid photo id", 400)

    container = get_container()
    try:
        photo, stream, length = container.get_project_photo_usecase.execute(
            pid_uuid, UUID(project_id), variant="thumbnail"
        )
    except PhotoNotFoundError:
        return _error_response("NOT_FOUND", "Photo not found", 404)

    # Thumbnail is always JPEG regardless of the original content_type.
    response = send_file(stream, mimetype="image/jpeg", as_attachment=False)
    _set_image_headers(response, length)
    return response


@project_photos_bp.route("/projects/<project_id>/photos/<photo_id>/original", methods=["GET"])
@openapi_doc(summary="Stream the original full-resolution image for a project photo", tags=["project_photos"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def get_photo_original(project_id: str, photo_id: str):
    try:
        pid_uuid = UUID(photo_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid photo id", 400)

    container = get_container()
    try:
        photo, stream, length = container.get_project_photo_usecase.execute(
            pid_uuid, UUID(project_id), variant="original"
        )
    except PhotoNotFoundError:
        return _error_response("NOT_FOUND", "Photo not found", 404)

    response = send_file(stream, mimetype=photo.content_type, as_attachment=False)
    _set_image_headers(response, length)
    return response


def _set_image_headers(response: Response, length: int) -> None:
    """Apply security and caching headers to streamed image responses.

    nosniff prevents browsers from MIME-sniffing user-controlled bytes into a
    renderable type (defense against stored-XSS via spoofed MIME on upload).
    A restrictive CSP (sandbox with no sources) further limits what a browser
    can do if it somehow misinterprets the content.
    """
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    # Cache immutable storage-keyed objects for 1 hour; storage keys are
    # write-once so the same key always returns the same bytes.
    response.headers["Cache-Control"] = "private, max-age=3600, immutable"
    if length:
        response.headers["Content-Length"] = str(length)


@project_photos_bp.route("/projects/<project_id>/photos/<photo_id>", methods=["PATCH"])
@openapi_doc(summary="Update caption and/or captured_at on a project photo", tags=["project_photos"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def update_project_photo(project_id: str, photo_id: str):
    try:
        pid_uuid = UUID(photo_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid photo id", 400)

    body = request.get_json(silent=True)
    if not body:
        return _error_response("INVALID_BODY", "Request body must be JSON", 400)

    try:
        params = UpdatePhotoBody.model_validate(body)
    except pydantic.ValidationError as exc:
        return _error_response("INVALID_PARAMS", str(exc), 422)

    if params.caption is None and params.captured_at is None:
        return _error_response("MISSING_FIELDS", "At least one of caption or captured_at must be provided", 422)

    container = get_container()
    project = container.project_repository.find_by_id(UUID(project_id))
    if project is None:
        return _error_response("NOT_FOUND", f"Project {project_id} not found", 404)

    requester_user_id = UUID(get_jwt_identity())
    is_admin = has_permission("*:*")

    # Build sentinel-aware kwargs so omitted fields are not overwritten.

    update_kwargs: dict = {}
    if params.caption is not None:
        update_kwargs["caption"] = params.caption
    if params.captured_at is not None:
        # Normalize to UTC if no tzinfo provided
        dt = params.captured_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        update_kwargs["captured_at"] = dt

    try:
        photo = container.update_project_photo_usecase.execute(
            pid_uuid,
            UUID(project_id),
            **update_kwargs,
            requester_user_id=requester_user_id,
            project=project,
            is_admin=is_admin,
        )
    except PhotoPermissionDeniedError:
        return _error_response("FORBIDDEN", "You are not permitted to update this photo", 403)
    except PhotoNotFoundError:
        return _error_response("NOT_FOUND", "Photo not found", 404)

    return jsonify(_serialize(photo)), 200


@project_photos_bp.route("/projects/<project_id>/photos/<photo_id>", methods=["DELETE"])
@openapi_doc(summary="Soft-delete a project photo", tags=["project_photos"])
@jwt_required()
@require_permission("project:read")
@require_project_access(write=False)
def delete_project_photo(project_id: str, photo_id: str):
    try:
        pid_uuid = UUID(photo_id)
    except ValueError:
        return _error_response("INVALID_ID", "Invalid photo id", 400)

    container = get_container()
    project = container.project_repository.find_by_id(UUID(project_id))
    if project is None:
        return _error_response("NOT_FOUND", f"Project {project_id} not found", 404)

    requester_user_id = UUID(get_jwt_identity())
    is_admin = has_permission("*:*")

    try:
        container.delete_project_photo_usecase.execute(
            pid_uuid,
            UUID(project_id),
            requester_user_id,
            project,
            is_admin=is_admin,
        )
    except PhotoPermissionDeniedError:
        return _error_response("FORBIDDEN", "You are not permitted to delete this photo", 403)
    except PhotoNotFoundError:
        return _error_response("NOT_FOUND", "Photo not found", 404)

    return "", 204
