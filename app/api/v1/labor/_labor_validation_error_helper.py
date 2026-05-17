"""Thin re-exports so existing labor imports keep working.

The canonical implementation now lives in ``app.api._helpers.validation_error``.
"""

from typing import Tuple

from flask import Response, jsonify
from pydantic import ValidationError

from app.api._helpers.validation_error import validation_error_response  # noqa: F401 – re-export
from app.api.v1.labor.schemas import ErrorResponse


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    """Create standardized error response."""
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code
