"""Thin re-exports so existing labor imports keep working.

The canonical implementation now lives in ``app.api._helpers.validation_error``.
"""

from datetime import date, datetime
from typing import Tuple

from flask import Response, jsonify
from app.api._helpers.validation_error import validation_error_response  # noqa: F401 – re-export
from app.api.v1.labor.schemas import ErrorResponse


def parse_iso_date(date_str: str) -> date:
    """Parse a YYYY-MM-DD string; ValueError carries a client-facing message."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")


def _error_response(error: str, message: str, status_code: int) -> Tuple[Response, int]:
    """Create standardized error response."""
    return jsonify(ErrorResponse(error=error, message=message, status_code=status_code).model_dump()), status_code
