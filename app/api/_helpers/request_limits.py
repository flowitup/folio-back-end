"""Cap a request's body per route, before anything reads it.

Werkzeug refuses a body over ``request.max_content_length`` with a 413 as soon as the
body is read (or at once, when the declared Content-Length is already over). A single
app-wide cap had to sit above the largest upload (a 150 MiB project document), so every
other request, including an unauthenticated one to a URL that does not exist, could make
a worker buffer and parse that much JSON before auth or rate limiting ran. This hook runs
first and gives each request the cap its route needs:

* multipart upload routes keep their documented limit (the use case's own file-size
  constant plus room for the multipart envelope and form fields);
* every other request (JSON, urlencoded, unknown URLs) gets ``DEFAULT_MAX_BODY_BYTES``.
"""

from __future__ import annotations

from typing import Callable

from flask import Flask, request
from werkzeug.exceptions import RequestEntityTooLarge

DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
# Room for the multipart envelope (boundaries, part headers) and the small form fields
# next to the file: the 150 MiB document limit has always been served by a 151 MiB cap.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
# bibliotheque.import_purchases is the one JSON route that legitimately outgrows the
# default: up to 1000 records, each at most ~3.3 KB of capped text fields (schema), so
# 1000 x 4 KiB covers the worst case with the JSON keys.
_IMPORT_RECORDS = 1000
_IMPORT_RECORD_BYTES = 4096


def _project_document_bytes() -> int:
    from app.application.project_documents import upload_project_document

    return upload_project_document.MAX_SIZE_BYTES


def _project_photo_bytes() -> int:
    from app.application.project_photos import upload_project_photo

    return max(upload_project_photo.MAX_SIZE_BYTES, upload_project_photo.MAX_VIDEO_SIZE_BYTES)


def _invoice_attachment_bytes() -> int:
    from app.application.invoice import upload_attachment

    return upload_attachment.MAX_FILE_SIZE_BYTES


def _chat_attachment_bytes() -> int:
    from app.application.chat import usecases

    return usecases.MAX_ATTACHMENT_BYTES


def _library_image_bytes() -> int:
    from app.application.bibliotheque import upload_product_image_usecase

    return upload_product_image_usecase.IMAGE_MAX_SIZE_BYTES


def _chiffrage_image_bytes() -> int:
    from app.application.chiffrage import article_image_usecases

    return article_image_usecases.IMAGE_MAX_SIZE_BYTES


def _analysis_report_bytes() -> int:
    from app.application.project_analyses import create_project_analysis_usecase

    return create_project_analysis_usecase.MAX_SIZE_BYTES


# Every route that reads request.files, by Flask endpoint, with the size of the file it
# accepts (tests/api/test_request_limits.py fails when a new upload route is missing).
MULTIPART_FILE_LIMITS: dict[str, Callable[[], int]] = {
    "project_documents.upload_project_document": _project_document_bytes,
    "project_photos.upload_project_photo": _project_photo_bytes,
    "invoices.upload_attachment": _invoice_attachment_bytes,
    "chat.send_message": _chat_attachment_bytes,
    "bibliotheque.upload_product_image": _library_image_bytes,
    "chiffrage.upload_article_image": _chiffrage_image_bytes,
    "project_analyses.create_analysis": _analysis_report_bytes,
}

JSON_LIMITS: dict[str, int] = {
    "bibliotheque.import_purchases": _IMPORT_RECORDS * _IMPORT_RECORD_BYTES,
}


def max_body_bytes(endpoint: str | None, mimetype: str) -> int:
    """The largest body (bytes) the request may carry."""
    if endpoint is None:
        return DEFAULT_MAX_BODY_BYTES
    if mimetype == "multipart/form-data" and endpoint in MULTIPART_FILE_LIMITS:
        return MULTIPART_FILE_LIMITS[endpoint]() + MULTIPART_OVERHEAD_BYTES
    return max(JSON_LIMITS.get(endpoint, 0), DEFAULT_MAX_BODY_BYTES)


def apply_request_body_limit() -> None:
    """``before_request`` hook, registered first: set this request's cap and refuse a declared excess outright."""
    limit = max_body_bytes(request.endpoint, request.mimetype)
    request.max_content_length = limit
    if request.content_length is not None and request.content_length > limit:
        raise RequestEntityTooLarge()


def register_request_limits(app: Flask) -> None:
    app.config["MAX_CONTENT_LENGTH"] = DEFAULT_MAX_BODY_BYTES
    app.before_request(apply_request_body_limit)
