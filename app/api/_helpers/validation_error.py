"""Shared helper for building validation-error responses.

Handles the edge-case where a Pydantic ``model_validator(mode='after')``
raises and the resulting error dict has ``loc=()`` (empty tuple).  A naive
``err.get("loc", ["unknown"])[-1]`` would raise ``IndexError`` on an empty
sequence; this module uses a safe access pattern instead.

Moved from ``app.api.v1.labor._labor_validation_error_helper`` so that
every blueprint (labor, invoices, notes, ...) can share the same code.
"""

from __future__ import annotations

from typing import Tuple

from flask import Response, jsonify
from pydantic import ValidationError


def safe_validation_fields(exc: ValidationError) -> list[str]:
    """Extract human-readable field names from a ``ValidationError``.

    Returns ``"unknown"`` for errors whose ``loc`` is empty (e.g. from
    ``model_validator(mode='after')``).
    """
    fields: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ("unknown",))
        fields.append(str(loc[-1]) if loc else "unknown")
    return fields


def validation_error_response(
    exc: ValidationError,
    *,
    status_code: int = 400,
    error_label: str = "ValidationError",
) -> Tuple[Response, int]:
    """Convert a Pydantic ``ValidationError`` to a JSON error response.

    Parameters
    ----------
    exc:
        The caught ``ValidationError``.
    status_code:
        HTTP status code (default 400; some blueprints use 422).
    error_label:
        The ``error`` key in the JSON body.
    """
    fields = safe_validation_fields(exc)
    body = {
        "error": error_label,
        "message": f"Invalid input: {', '.join(fields)}",
        "status_code": status_code,
    }
    return jsonify(body), status_code
