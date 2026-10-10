"""Refuse a NUL character (``\\x00``) anywhere in an API request's text.

Postgres cannot store NUL: psycopg2 refuses it in a text parameter (ValueError "A
string literal cannot contain NUL (0x00) characters.") and Postgres refuses
``\\u0000`` inside JSONB (UntranslatableCharacter). Both came out of a flush deep
inside about twenty write endpoints as a 500, or as a 400 carrying the driver's
text. No real input contains NUL, so the request is refused before any route runs.
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, request
from sqlalchemy.exc import DataError

NUL_MESSAGE = "Text contains an invalid character (NUL)."

_FORM_MIMETYPES = ("multipart/form-data", "application/x-www-form-urlencoded")


def _has_nul(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(key) or _has_nul(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_nul(item) for item in value)
    return False


def _request_has_nul() -> bool:
    if any(_has_nul(key) or _has_nul(value) for key, value in request.args.items(multi=True)):
        return True
    if request.view_args and _has_nul(list(request.view_args.values())):
        return True
    if request.is_json and _has_nul(request.get_json(silent=True)):
        return True
    if request.mimetype in _FORM_MIMETYPES:
        if any(_has_nul(key) or _has_nul(value) for key, value in request.form.items(multi=True)):
            return True
        if any(_has_nul(upload.filename or "") for _, upload in request.files.items(multi=True)):
            return True
    return False


def nul_error_response() -> tuple[Response, int]:
    return jsonify({"error": "ValidationError", "message": NUL_MESSAGE, "status_code": 400}), 400


def reject_nul_characters() -> tuple[Response, int] | None:
    """``before_request`` hook: a 400 when an /api/ request carries a NUL character."""
    if request.path.startswith("/api/") and _request_has_nul():
        return nul_error_response()
    return None


def is_nul_storage_error(exc: BaseException | None) -> bool:
    """Whether ``exc`` is the database refusing a NUL the hook could not see (e.g. a header)."""
    if isinstance(exc, ValueError):
        return "NUL (0x00)" in str(exc)
    if isinstance(exc, DataError):
        return "unsupported Unicode escape sequence" in str(exc.orig)
    return False


def register_nul_guard(app: Flask) -> None:
    app.before_request(reject_nul_characters)
