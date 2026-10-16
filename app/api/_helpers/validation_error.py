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

from app.api._helpers.pydantic_errors import validation_message


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


def _rule_text(err: dict) -> str:
    """The rule a value broke, in words a client can show ("80 characters max"), never the input."""
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "missing":
        return "required"
    if kind == "string_too_long":
        return f"{ctx.get('max_length')} characters max"
    if kind == "string_too_short":
        return "required" if ctx.get("min_length") == 1 else f"at least {ctx.get('min_length')} characters"
    if kind == "string_pattern_mismatch":
        return "invalid format"
    if kind in ("string_type", "str_type"):
        return "must be text"
    if kind == "model_type" or kind == "dict_type":
        return "the body must be a JSON object"
    return str(err.get("msg", "invalid")).removeprefix("Value error, ")


def field_rule_message(exc: ValidationError, rules: "dict[str, str] | None" = None) -> str:
    """'Invalid input: <field>: <rule>; ...' naming only the fields that failed.

    ``rules`` overrides the text for a field (e.g. ``{"code": "6 digits"}``) where the generic
    rule would be cryptic (a regex). Body-level errors (empty ``loc``) give their rule alone.
    """
    parts: list[str] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        field = ".".join(str(p) for p in loc)
        rule = (rules or {}).get(field) or _rule_text(err)
        text = f"{field}: {rule}" if field else rule
        if text not in parts:
            parts.append(text)
    return "Invalid input: " + "; ".join(parts)


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
    body = {
        "error": error_label,
        # Field and reason, e.g. "shift_type: Field required", or a body-level rule's own text.
        "message": validation_message(exc),
        "status_code": status_code,
    }
    return jsonify(body), status_code
