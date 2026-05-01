"""Shared helper to convert Pydantic ValidationError → JSON-safe error tuple."""

from typing import Tuple

from flask import jsonify
from pydantic import ValidationError


def format_validation_error(exc: ValidationError) -> Tuple[object, int]:
    """Return a (response, status_code) tuple for a 422 JSON response.

    Builds a JSON-safe error list — exc.errors() may embed ValueError objects
    in the 'ctx' field when model_validators raise, which Flask's jsonify
    cannot serialise. Returns 422 with {error, message, details}.
    """
    safe_errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    detail = "; ".join(f"{('.'.join(str(loc) for loc in e['loc']) or 'value')}: {e['msg']}" for e in safe_errors)
    return jsonify({"error": "validation_error", "details": safe_errors, "message": detail}), 422
