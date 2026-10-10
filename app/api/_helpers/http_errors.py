"""JSON bodies for HTTP errors raised outside a route's own error handling.

Werkzeug answers a malformed JSON body (400), a wrong Content-Type (415), an
unknown URL or a malformed ``<uuid:...>`` path segment (404), a wrong method
(405) and any unhandled exception (500) with an HTML page. API clients parse
every error as ``{error, message, status_code}``, so under ``/api/`` those
errors keep that envelope too.
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException, InternalServerError

from app.api._helpers.nul_guard import is_nul_storage_error, nul_error_response


def _is_api_request() -> bool:
    return request.path.startswith("/api/")


def _json_http_error(exc: HTTPException) -> HTTPException | tuple[Response, int]:
    if not _is_api_request() or exc.code is None:
        return exc
    if isinstance(exc, InternalServerError) and is_nul_storage_error(exc.original_exception):
        # A NUL the request guard could not see reached the database: still the client's input.
        return nul_error_response()
    body = {
        "error": exc.name.replace(" ", ""),
        "message": exc.description or exc.name,
        "status_code": exc.code,
    }
    response = jsonify(body)
    # Keep headers such as Allow (405) or Retry-After (429).
    for key, value in exc.get_headers():
        if key.lower() != "content-type":
            response.headers[key] = value
    return response, exc.code


def register_json_error_handlers(app: Flask) -> None:
    """Answer HTTP errors on API paths with the JSON error envelope."""
    app.register_error_handler(HTTPException, _json_http_error)
