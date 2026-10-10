"""Shared helper to convert Pydantic ValidationError → JSON-safe error tuple."""

from typing import Tuple

from flask import jsonify
from pydantic import ValidationError


def _error_text(error: dict) -> str:
    """The "field: reason" text of one error; just the reason for a body-level rule (empty loc)."""
    if error["type"] == "model_type":
        # Pydantic's text names the schema class ("... instance of CreateXRequest").
        reason = "Input should be an object"
    else:
        reason = error["msg"].removeprefix("Value error, ")
    field = ".".join(str(loc) for loc in error["loc"])
    return f"{field}: {reason}" if field else reason


def validation_message(exc: ValidationError) -> str:
    """One line per failed field ("field: reason"), without the input or docs links.

    ``str(exc)`` would echo the submitted value, the model name and an
    errors.pydantic.dev URL to the end user. A model-level validator has no
    field, so its reason stands alone ("from must be <= to").
    """
    return "; ".join(_error_text(e) for e in exc.errors())


def format_validation_error(exc: ValidationError) -> Tuple[object, int]:
    """Return a (response, status_code) tuple for a 422 JSON response.

    Builds a JSON-safe error list — exc.errors() may embed ValueError objects
    in the 'ctx' field when model_validators raise, which Flask's jsonify
    cannot serialise. Returns 422 with {error, message, details}.
    """
    safe_errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return jsonify({"error": "validation_error", "details": safe_errors, "message": validation_message(exc)}), 422
