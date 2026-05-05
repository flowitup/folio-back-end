"""Billing document template API routes.

Endpoints (5):
  GET    /billing-document-templates                → list (jwt)
  POST   /billing-document-templates                → create (jwt, 10/min)
  GET    /billing-document-templates/<template_id>  → get (jwt + owner)
  PUT    /billing-document-templates/<template_id>  → update (jwt + owner, 30/min)
  DELETE /billing-document-templates/<template_id>  → delete (jwt + owner)

Decorator order: @jwt_required() BEFORE @limiter.limit(...).
"""

from __future__ import annotations

import dataclasses
from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.billing import billing_templates_bp
from app.api.v1.billing.decorators import require_billing_template_owner
from app.api.v1.billing.schemas import CreateTemplateRequest, UpdateTemplateRequest
from app.application.billing import (
    CreateTemplateInput,
    ItemInput,
    UpdateTemplateInput,
    BillingTemplateNotFoundError,
    ForbiddenBillingDocumentError,
)
from app.domain.billing.enums import BillingDocumentKind
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(error: str, message: str, status: int) -> Tuple[Response, int]:
    return jsonify({"error": error, "message": message}), status


def _items_from_schema(raw_items) -> list[ItemInput]:
    return [
        ItemInput(
            description=it.description,
            quantity=it.quantity,
            unit_price=it.unit_price,
            vat_rate=it.vat_rate,
        )
        for it in raw_items
    ]


def _tpl_to_json(dto) -> dict:
    return dataclasses.asdict(dto)


# ---------------------------------------------------------------------------
# List templates
# ---------------------------------------------------------------------------


@billing_templates_bp.route("/billing-document-templates", methods=["GET"])
@jwt_required()
def list_billing_templates():
    """List all billing templates for the authenticated user.

    Optional query param: kind (devis | facture).
    """
    kind_str = request.args.get("kind", "").strip()
    kind = None
    if kind_str:
        try:
            kind = BillingDocumentKind(kind_str)
        except ValueError:
            return _err("ValidationError", f"Invalid kind: {kind_str!r}", 400)

    user_id = UUID(get_jwt_identity())
    templates = get_container().list_billing_templates_usecase.execute(
        user_id=user_id,
        kind=kind,
    )
    return jsonify({"items": [_tpl_to_json(t) for t in templates], "total": len(templates)})


# ---------------------------------------------------------------------------
# Create template
# ---------------------------------------------------------------------------


@billing_templates_bp.route("/billing-document-templates", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
def create_billing_template():
    """Create a new billing document template."""
    try:
        body = CreateTemplateRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    inp = CreateTemplateInput(
        user_id=user_id,
        kind=BillingDocumentKind(body.kind),
        name=body.name,
        items=_items_from_schema(body.items),
        notes=body.notes,
        terms=body.terms,
        default_vat_rate=body.default_vat_rate,
    )

    from app import db

    try:
        result = get_container().create_billing_template_usecase.execute(inp, db.session)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_tpl_to_json(result)), 201


# ---------------------------------------------------------------------------
# Get template
# ---------------------------------------------------------------------------


@billing_templates_bp.route("/billing-document-templates/<template_id>", methods=["GET"])
@jwt_required()
@require_billing_template_owner
def get_billing_template(template_id: str, billing_template):
    """Retrieve a single billing template by ID (ownership enforced by decorator)."""
    from app.application.billing.dtos import BillingTemplateResponse

    return jsonify(_tpl_to_json(BillingTemplateResponse.from_entity(billing_template)))


# ---------------------------------------------------------------------------
# Update template
# ---------------------------------------------------------------------------


@billing_templates_bp.route("/billing-document-templates/<template_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_billing_template_owner
def update_billing_template(template_id: str, billing_template):
    """Partially update a billing document template."""
    try:
        body = UpdateTemplateRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    inp = UpdateTemplateInput(
        id=billing_template.id,
        user_id=user_id,
        name=body.name,
        items=_items_from_schema(body.items) if body.items is not None else None,
        notes=body.notes,
        terms=body.terms,
        default_vat_rate=body.default_vat_rate,
    )

    from app import db

    try:
        result = get_container().update_billing_template_usecase.execute(inp, db.session)
    except BillingTemplateNotFoundError:
        return _err("NotFound", f"Billing template {template_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing template {template_id} not found", 404)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_tpl_to_json(result))


# ---------------------------------------------------------------------------
# Delete template
# ---------------------------------------------------------------------------


@billing_templates_bp.route("/billing-document-templates/<template_id>", methods=["DELETE"])
@jwt_required()
@require_billing_template_owner
def delete_billing_template(template_id: str, billing_template):
    """Delete a billing document template (ownership enforced by decorator)."""
    from app import db

    user_id = UUID(get_jwt_identity())
    try:
        get_container().delete_billing_template_usecase.execute(billing_template.id, user_id, db.session)
    except BillingTemplateNotFoundError:
        return _err("NotFound", f"Billing template {template_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing template {template_id} not found", 404)

    return "", 204
