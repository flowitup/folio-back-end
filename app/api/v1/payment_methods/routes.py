"""Payment methods API routes.

All endpoints are nested under /companies/<company_id>/payment-methods.

Decorator order: @jwt_required() BEFORE @limiter.limit(...).
"""

from __future__ import annotations

import dataclasses
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.payment_methods import payment_methods_bp
from app.api.v1.payment_methods.schemas import (
    CreatePaymentMethodRequest,
    UpdatePaymentMethodRequest,
)
from app.application.payment_methods.dtos import CreatePaymentMethodInput, UpdatePaymentMethodInput
from app.domain.companies.exceptions import ForbiddenCompanyError
from app.domain.payment_methods.exceptions import (
    BuiltinPaymentMethodDeletionError,
    PaymentMethodAlreadyExistsError,
    PaymentMethodNotFoundError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _err(error: str, message: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), status


# ---------------------------------------------------------------------------
# GET /companies/<company_id>/payment-methods
# ---------------------------------------------------------------------------


@payment_methods_bp.route("/companies/<company_id>/payment-methods", methods=["GET"])
@jwt_required()
def list_payment_methods(company_id: str):
    """List payment methods for a company.

    Active methods only by default. Admin may pass ?include_inactive=true.
    Returns Cache-Control: no-cache, must-revalidate.
    """
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("not_found", f"Company {company_id} not found", 404)

    include_inactive = request.args.get("include_inactive", "false").lower() in ("1", "true", "yes")
    requester_id = UUID(get_jwt_identity())

    try:
        results = get_container().list_payment_methods_usecase.execute(
            requester_id=requester_id,
            company_id=company_uuid,
            include_inactive=include_inactive,
        )
    except ForbiddenCompanyError:  # pragma: no cover
        # NOTE: ListPaymentMethodsUseCase never raises ForbiddenCompanyError; this
        # branch is a defensive catch kept for future permission-gate changes.
        return _err("permission_denied", "You do not have permission to view payment methods", 403)

    items = [dataclasses.asdict(r) for r in results]
    resp = jsonify({"items": items})
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# ---------------------------------------------------------------------------
# POST /companies/<company_id>/payment-methods
# ---------------------------------------------------------------------------


@payment_methods_bp.route("/companies/<company_id>/payment-methods", methods=["POST"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def create_payment_method(company_id: str):
    """Create a new payment method for a company (admin only)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("not_found", f"Company {company_id} not found", 404)

    try:
        body = CreatePaymentMethodRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    requester_id = UUID(get_jwt_identity())
    inp = CreatePaymentMethodInput(
        requester_id=requester_id,
        company_id=company_uuid,
        label=body.label,
    )

    from app import db

    try:
        result = get_container().create_payment_method_usecase.execute(inp, db.session)
    except ForbiddenCompanyError:
        return _err("permission_denied", "Admin permission required", 403)
    except PaymentMethodAlreadyExistsError:
        return _err("duplicate_label", "A payment method with that label already exists", 409)

    return jsonify(dataclasses.asdict(result)), 201


# ---------------------------------------------------------------------------
# PATCH /companies/<company_id>/payment-methods/<payment_method_id>
# ---------------------------------------------------------------------------


@payment_methods_bp.route(
    "/companies/<company_id>/payment-methods/<payment_method_id>",
    methods=["PATCH"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def update_payment_method(company_id: str, payment_method_id: str):
    """Partially update a payment method (admin only).

    Label rename is allowed on builtins. Deactivating a builtin returns 409.
    """
    try:
        company_uuid = UUID(company_id)  # noqa: F841 — validated for consistency
        method_uuid = UUID(payment_method_id)
    except ValueError:
        return _err("not_found", "Payment method not found", 404)

    try:
        body = UpdatePaymentMethodRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    requester_id = UUID(get_jwt_identity())

    # Guard: is_active=false on a builtin raises BuiltinPaymentMethodDeletionError
    # inside the use-case — caught below with reason="deactivate".
    inp = UpdatePaymentMethodInput(
        requester_id=requester_id,
        payment_method_id=method_uuid,
        label=body.label,
        is_active=body.is_active,
    )

    from app import db

    try:
        result = get_container().update_payment_method_usecase.execute(inp, db.session)
    except ForbiddenCompanyError:
        return _err("permission_denied", "Admin permission required", 403)
    except PaymentMethodNotFoundError:
        return _err("not_found", "Payment method not found", 404)
    except PaymentMethodAlreadyExistsError:
        return _err("duplicate_label", "A payment method with that label already exists", 409)
    except BuiltinPaymentMethodDeletionError:
        return (
            jsonify(
                {
                    "error": "builtin_protected",
                    "message": "Builtin payment methods cannot be deactivated",
                    "reason": "deactivate",
                }
            ),
            409,
        )

    return jsonify(dataclasses.asdict(result))


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id>/payment-methods/<payment_method_id>
# ---------------------------------------------------------------------------


@payment_methods_bp.route(
    "/companies/<company_id>/payment-methods/<payment_method_id>",
    methods=["DELETE"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def delete_payment_method(company_id: str, payment_method_id: str):
    """Soft-delete a payment method (admin only). Returns 204."""
    try:
        _company_uuid = UUID(company_id)  # noqa: F841 — validated for consistency
        method_uuid = UUID(payment_method_id)
    except ValueError:
        return _err("not_found", "Payment method not found", 404)

    requester_id = UUID(get_jwt_identity())

    from app import db

    try:
        get_container().delete_payment_method_usecase.execute(
            requester_id=requester_id,
            payment_method_id=method_uuid,
            db_session=db.session,
        )
    except ForbiddenCompanyError:
        return _err("permission_denied", "Admin permission required", 403)
    except PaymentMethodNotFoundError:
        return _err("not_found", "Payment method not found", 404)
    except BuiltinPaymentMethodDeletionError:
        return (
            jsonify(
                {
                    "error": "builtin_protected",
                    "message": "Builtin payment methods cannot be deleted",
                    "reason": "delete",
                }
            ),
            409,
        )

    return "", 204
