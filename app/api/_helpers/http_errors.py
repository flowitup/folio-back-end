"""JSON bodies for HTTP errors raised outside a route's own error handling.

Werkzeug answers a malformed JSON body (400), a wrong Content-Type (415), an
unknown URL or a malformed ``<uuid:...>`` path segment (404), a wrong method
(405) and any unhandled exception (500) with an HTML page. API clients parse
every error as ``{error, message, status_code}``, so under ``/api/`` those
errors keep that envelope too.
"""

from __future__ import annotations

import time

from flask import Flask, Response, jsonify, request
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import HTTPException, InternalServerError

from app.api._helpers.nul_guard import is_nul_storage_error, nul_error_response
from app.api._helpers.otp_messages import otp_throttled
from app.domain.exceptions.auth_exceptions import OtpThrottledError
from app.infrastructure.rate_limiter import limiter

# The routes that text a code: a limiter 429 here is answered like the use cases' own throttling.
_CODE_REQUEST_PATHS = ("/auth/otp/request", "/auth/signup/request", "/invitations/accept/request-code")


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


def _rate_limited(exc: RateLimitExceeded):
    """A limiter 429 on a code-request route carries Retry-After and, for an hour or more, ``OtpHourlyLimit``.

    That is the shape the web and mobile sign-in screens read to say how long to wait; without it a
    user who hit an hourly cap was told "wait a minute" and kept retrying. Other routes keep the default.
    """
    if not (_is_api_request() and request.path.endswith(_CODE_REQUEST_PATHS)):
        return _json_http_error(exc)
    window = exc.limit.limit.GRANULARITY.seconds
    wait = window
    try:
        current = limiter.current_limit
        if current is not None and current.breached:
            wait = min(window, max(1, current.reset_at - int(time.time())))
    except Exception:  # never let the hint break the 429 itself
        pass
    error, message, headers = otp_throttled(
        OtpThrottledError("Rate limit exceeded", retry_after_seconds=wait, hourly_limit=window >= 3600)
    )
    response = jsonify({"error": error, "message": message, "status_code": 429})
    response.headers.update(headers)
    return response, 429


def register_json_error_handlers(app: Flask) -> None:
    """Answer HTTP errors on API paths with the JSON error envelope."""
    app.register_error_handler(HTTPException, _json_http_error)
    app.register_error_handler(RateLimitExceeded, _rate_limited)
