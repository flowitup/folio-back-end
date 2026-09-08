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
from app.api.openapi import openapi_doc
from app.api.v1.ops_context import is_platform_ops
from app.api.v1.billing import billing_templates_bp
from app.api.v1.billing.decorators import require_billing_template_owner
from app.api.v1.billing.schemas import CreateTemplateRequest, UpdateTemplateRequest
from app.application.billing import (
    CreateTemplateInput,
    ItemInput,
    UpdateTemplateInput,
    BillingTemplateNotFoundError,
    BillingTemplateNameConflictError,
    ForbiddenBillingDocumentError,
)
from app.application.billing.ports import is_company_admin
from app.domain.billing.enums import BillingDocumentKind
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _has_superadmin() -> bool:
    """True for platform ops (flowitup support), never for a company admin."""
    return is_platform_ops()


def _resolve_template_company_scope(caller_id: UUID, requested_raw: "str | None"):
    """Resolve the company scope for list/create (Phase 2).

    Returns (company_id_or_None, error_response_or_None). An explicit
    `requested_raw` (query param or request body field) must be a company the
    caller administers (or superadmin). With none given, falls back to the
    caller's own admin company if they have exactly one primary — this keeps
    every caller with no company context working exactly as before Phase 2
    (unscoped, user-owned templates).
    """
    container = get_container()
    access_repo = getattr(container, "user_company_access_repo", None)

    if requested_raw:
        try:
            requested = UUID(requested_raw)
        except ValueError:
            return None, _err("ValidationError", f"Invalid company id: {requested_raw!r}", 400)
        if not _has_superadmin() and not is_company_admin(access_repo, caller_id, requested):
            return None, _err("Forbidden", f"Admin role required for company {requested_raw}", 403)
        return requested, None

    authz_reader = getattr(container, "authz_reader", None)
    if authz_reader is not None:
        admin_ids = authz_reader.admin_company_ids(caller_id)
        primary = authz_reader.primary_company_id(caller_id)
        if primary is not None and primary in admin_ids:
            return primary, None
    return None, None


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
@openapi_doc(summary="List all billing templates for the authenticated user", tags=["billing"])
@jwt_required()
def list_billing_templates():
    """List billing templates for the authenticated user, or for a company (Phase 2).

    Optional query params: kind (devis | facture), company_id — when given,
    the caller must administer that company (or be superadmin); the
    response then lists every template of the company, not just the
    caller's own.
    """
    kind_str = request.args.get("kind", "").strip()
    kind = None
    if kind_str:
        try:
            kind = BillingDocumentKind(kind_str)
        except ValueError:
            return _err("ValidationError", f"Invalid kind: {kind_str!r}", 400)

    user_id = UUID(get_jwt_identity())
    company_id, error = _resolve_template_company_scope(user_id, request.args.get("company_id"))
    if error is not None:
        return error

    templates = get_container().list_billing_templates_usecase.execute(
        user_id=user_id,
        kind=kind,
        company_id=company_id,
    )
    return jsonify({"items": [_tpl_to_json(t) for t in templates], "total": len(templates)})


# ---------------------------------------------------------------------------
# Create template
# ---------------------------------------------------------------------------


@billing_templates_bp.route("/billing-document-templates", methods=["POST"])
@openapi_doc(
    summary="Create a new billing document template",
    request=CreateTemplateRequest,
    tags=["billing"],
)
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
def create_billing_template():
    """Create a new billing document template."""
    try:
        body = CreateTemplateRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    company_id, error = _resolve_template_company_scope(user_id, body.company_id)
    if error is not None:
        return error

    inp = CreateTemplateInput(
        user_id=user_id,
        kind=BillingDocumentKind(body.kind),
        name=body.name,
        items=_items_from_schema(body.items),
        notes=body.notes,
        terms=body.terms,
        default_vat_rate=body.default_vat_rate,
        company_id=company_id,
    )

    from app import db

    try:
        result = get_container().create_billing_template_usecase.execute(inp, db.session)
    except BillingTemplateNameConflictError as exc:
        return _err("Conflict", str(exc), 409)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_tpl_to_json(result)), 201


# ---------------------------------------------------------------------------
# Get template
# ---------------------------------------------------------------------------


@billing_templates_bp.route("/billing-document-templates/<template_id>", methods=["GET"])
@openapi_doc(summary="Retrieve a single billing template by ID", tags=["billing"])
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
@openapi_doc(
    summary="Partially update a billing document template",
    request=UpdateTemplateRequest,
    tags=["billing"],
)
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
@openapi_doc(summary="Delete a billing document template", tags=["billing"])
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
