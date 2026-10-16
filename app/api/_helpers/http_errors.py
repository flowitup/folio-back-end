"""JSON bodies for HTTP errors raised outside a route's own error handling.

Werkzeug answers a malformed JSON body (400), a wrong Content-Type (415), an
unknown URL or a malformed ``<uuid:...>`` path segment (404), a wrong method
(405) and any unhandled exception (500) with an HTML page. API clients parse
every error as ``{error, message, status_code}``, so under ``/api/`` those
errors keep that envelope too.
"""

from __future__ import annotations

import math
import time

from flask import Flask, Response, jsonify, request
from flask_limiter.errors import RateLimitExceeded
from werkzeug.exceptions import HTTPException, InternalServerError

from app.api._helpers.nul_guard import is_nul_storage_error, nul_error_response
from app.api._helpers.otp_messages import otp_throttled
from app.domain.exceptions.auth_exceptions import OTP_SCOPE_NETWORK, OtpThrottledError
from app.infrastructure.rate_limiter import limiter, remote_address_bucket

# The routes that text a code: a limiter 429 here is answered like the use cases' own throttling.
_CODE_REQUEST_PATHS = (
    "/auth/otp/request",
    "/auth/signup/request",
    "/invitations/accept/request-code",
    "/auth/me/phone/request-code",
)
# The routes that check a code: their per-address 429 must not read as "a code was sent".
_CODE_VERIFY_PATHS = (
    "/auth/otp/verify",
    "/auth/signup/verify",
    "/invitations/accept",
)


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


def _retry_after_seconds(exc: RateLimitExceeded) -> int:
    """Seconds until the breached limit lets the caller in again (its window when unknown)."""
    window = int(exc.limit.limit.GRANULARITY.seconds * exc.limit.limit.multiples)
    wait = window
    try:
        current = limiter.current_limit
        if current is not None and current.breached:
            wait = min(window, max(1, int(current.reset_at) - int(time.time())))
    except Exception:  # never let the hint break the 429 itself
        pass
    return max(1, wait)


def _wait_text(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} second" + ("" if seconds == 1 else "s")
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def _is_network_limit(exc: RateLimitExceeded) -> bool:
    """True when the breached limit is keyed on the caller's address (shared by a whole Wi-Fi)."""
    return getattr(exc.limit, "key_func", None) is remote_address_bucket


def _rate_limited(exc: RateLimitExceeded):
    """Every limiter 429 under /api/ says how long to wait: a ``Retry-After`` header, a
    ``retry_after`` field and a readable message (never the limiter's internal "5 per 1 minute").

    On the routes that text a code, the answer also tells WHO hit the cap: the number itself
    (``OtpHourlyLimit`` / "a code was sent recently"), or the network the request comes from
    (``NetworkThrottled``), so a worker whose colleague on the same site Wi-Fi used up the
    per-address cap is not told a code was just sent to them.
    """
    if not _is_api_request():
        return _json_http_error(exc)
    wait = _retry_after_seconds(exc)
    headers = {"Retry-After": str(wait)}
    on_code_route = request.path.endswith(_CODE_REQUEST_PATHS + _CODE_VERIFY_PATHS)
    if on_code_route and _is_network_limit(exc):
        error, message, headers = otp_throttled(
            OtpThrottledError("Rate limit exceeded", retry_after_seconds=wait, scope=OTP_SCOPE_NETWORK)
        )
    elif request.path.endswith(_CODE_REQUEST_PATHS):
        window = int(exc.limit.limit.GRANULARITY.seconds * exc.limit.limit.multiples)
        error, message, headers = otp_throttled(
            OtpThrottledError("Rate limit exceeded", retry_after_seconds=wait, hourly_limit=window >= 3600)
        )
    else:
        error = "TooManyRequests"
        message = f"Too many requests. Try again in {_wait_text(wait)}."
    response = jsonify({"error": error, "message": message, "status_code": 429, "retry_after": wait})
    response.headers.update(headers)
    return response, 429


def register_json_error_handlers(app: Flask) -> None:
    """Answer HTTP errors on API paths with the JSON error envelope."""
    app.register_error_handler(HTTPException, _json_http_error)
    app.register_error_handler(RateLimitExceeded, _rate_limited)
