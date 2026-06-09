"""Materials & services expenses API routes.

Endpoints (2):
  GET  /billing/materials-expenses                    → list (jwt, refundable filter)
  PATCH /billing/materials-expenses/<invoice_id>      → set refundable_status (jwt, 30/min)

Company-admin gate mirrors billing-documents authz.
Decorator order: @jwt_required() BEFORE @limiter.limit(...).
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from pydantic import BaseModel, field_validator

from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.billing import billing_documents_bp
from app.domain.billing.exceptions import ForbiddenCompanyBillingError
from app.domain.entities.invoice import RefundableStatus
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, InvoiceNotFoundError
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _err(error: str, message: str, status: int) -> Tuple[Response, int]:
    return jsonify({"error": error, "message": message}), status


# ---------------------------------------------------------------------------
# Pydantic schema for PATCH body
# ---------------------------------------------------------------------------


class SetRefundableStatusSchema(BaseModel):
    """Body for PATCH /billing/materials-expenses/<invoice_id>.

    refundable_status=null clears the field (moves to not-refundable).
    """

    refundable_status: Optional[str] = None

    @field_validator("refundable_status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        valid = {s.value for s in RefundableStatus}
        if v not in valid:
            raise ValueError(f"Invalid refundable_status {v!r}. Allowed: {sorted(valid)}")
        return v


# ---------------------------------------------------------------------------
# GET /billing/materials-expenses
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing/materials-expenses", methods=["GET"])
@jwt_required()
@limiter.limit("60 per minute", key_func=jwt_user_key)
def list_materials_expenses():
    """List materials & services invoices accessible to the caller.

    Query params:
      refundable  : true | false (default: true)
      company_id  : optional UUID — restrict to one company
      limit       : int (default 50, max 200)
      offset      : int (default 0)

    Response: { items: [...], total, limit, offset }
    """
    # Parse refundable param — default True
    refundable_str = request.args.get("refundable", "true").strip().lower()
    if refundable_str == "true":
        refundable: Optional[bool] = True
    elif refundable_str == "false":
        refundable = False
    else:
        return _err("ValidationError", "refundable must be 'true' or 'false'", 400)

    company_id: Optional[UUID] = None
    company_id_str = request.args.get("company_id")
    if company_id_str:
        try:
            company_id = UUID(company_id_str)
        except ValueError:
            return _err("ValidationError", "Invalid company_id", 400)

    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return _err("ValidationError", "limit and offset must be integers", 400)

    user_id = UUID(get_jwt_identity())
    is_superadmin = "*:*" in get_jwt().get("permissions", [])

    try:
        result = get_container().list_materials_expenses_usecase.execute(
            user_id=user_id,
            is_superadmin=is_superadmin,
            company_id=company_id,
            refundable=refundable,
            limit=limit,
            offset=offset,
        )
    except ForbiddenCompanyBillingError:
        return _err("Forbidden", "You do not have admin access to the requested company", 403)

    return jsonify(
        {
            "items": [dataclasses.asdict(item) for item in result.items],
            "total": result.total,
            "limit": result.limit,
            "offset": result.offset,
        }
    )


# ---------------------------------------------------------------------------
# PATCH /billing/materials-expenses/<invoice_id>
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing/materials-expenses/<invoice_id>", methods=["PATCH"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def set_materials_expense_refundable_status(invoice_id: str):
    """Set or clear the refundable_status on a materials_services invoice.

    Body: { "refundable_status": "refundable" | "refund_pending" | "refunded" | null }

    Error mapping:
      400 — invalid invoice_id UUID, wrong invoice type, invalid status value
      403 — caller is not company-admin for the invoice's project company
      404 — invoice not found
    """
    try:
        invoice_uuid = UUID(invoice_id)
    except ValueError:
        return _err("ValidationError", f"Invalid invoice_id: {invoice_id!r}", 400)

    raw_body = request.get_json(force=True, silent=True) or {}

    # Pydantic validates the status value
    try:
        body = SetRefundableStatusSchema.model_validate(raw_body)
    except Exception as exc:
        return _err("ValidationError", str(exc), 400)

    user_id = UUID(get_jwt_identity())
    is_superadmin = "*:*" in get_jwt().get("permissions", [])

    try:
        updated = get_container().set_refundable_status_usecase.execute(
            user_id=user_id,
            is_superadmin=is_superadmin,
            invoice_id=invoice_uuid,
            refundable_status=body.refundable_status,
        )
    except InvoiceNotFoundError:
        return _err("NotFound", f"Invoice {invoice_id} not found", 404)
    except InvalidInvoiceDataError as exc:
        return _err("ValidationError", str(exc), 400)
    except ForbiddenCompanyBillingError:
        return _err("Forbidden", "You do not have admin access to this invoice's company", 403)

    return jsonify(dataclasses.asdict(updated))
