"""Shared helper for building validation error responses in labor routes."""

from typing import Tuple

from flask import Response, jsonify
from pydantic import ValidationError

from app.api.v1.labor.schemas import ErrorResponse


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    """Create standardized error response."""
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code


def validation_error_response(e: ValidationError) -> Tuple[Response, int]:
    """Create validation error response from Pydantic error.

    Handles model_validator errors which return loc=() (empty tuple).
    Using [-1] on an empty tuple raises IndexError, so we guard with a conditional.
    """
    error_fields = []
    for err in e.errors():
        loc = err.get("loc", ("unknown",))
        error_fields.append(loc[-1] if loc else "unknown")
    return _error_response("ValidationError", f"Invalid input: {', '.join(str(f) for f in error_fields)}", 400)
