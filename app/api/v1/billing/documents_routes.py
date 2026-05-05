"""Billing document API routes.

Endpoints (10):
  GET    /billing-documents                          → list (jwt, kind required)
  POST   /billing-documents                          → create (jwt, 10/min)
  GET    /billing-documents/<doc_id>                 → get (jwt + owner)
  PUT    /billing-documents/<doc_id>                 → update (jwt + owner, 30/min)
  DELETE /billing-documents/<doc_id>                 → delete (jwt + owner)
  POST   /billing-documents/<doc_id>/clone           → clone (jwt + owner, 10/min)
  POST   /billing-documents/<doc_id>/convert-to-facture → convert (jwt + owner, 10/min)
  PATCH  /billing-documents/<doc_id>/status          → status update (jwt + owner, 30/min)
  GET    /billing-documents/<doc_id>/pdf             → pdf download (jwt + owner, 5/min)
  POST   /billing-documents/from-template/<template_id> → apply template (jwt, 10/min)

Decorator order: @jwt_required() BEFORE @limiter.limit(...).
"""

from __future__ import annotations

import dataclasses
from io import BytesIO
from typing import Tuple
from urllib.parse import quote
from uuid import UUID

from flask import Response, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.billing import billing_documents_bp
from app.api.v1.billing.decorators import require_billing_document_owner
from app.api.v1.billing.schemas import (
    ApplyTemplateRequest,
    CloneRequest,
    ConvertRequest,
    CreateBillingDocumentRequest,
    UpdateBillingDocumentRequest,
    UpdateStatusRequest,
)
from app.application.billing import (
    ApplyTemplateInput,
    CloneBillingDocumentInput,
    ConvertDevisToFactureInput,
    CreateBillingDocumentInput,
    ItemInput,
    UpdateBillingDocumentInput,
    UpdateStatusInput,
    BillingDocumentNotFoundError,
    BillingTemplateNotFoundError,
    BillingNumberCollisionError,
    DevisAlreadyConvertedError,
    ForbiddenBillingDocumentError,
    InvalidStatusTransitionError,
    MissingCompanyProfileError,
)
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
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


def _doc_to_json(dto) -> dict:
    """Convert a BillingDocumentResponse dataclass to a JSON-safe dict."""
    return dataclasses.asdict(dto)


# ---------------------------------------------------------------------------
# List billing documents
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents", methods=["GET"])
@jwt_required()
def list_billing_documents():
    """List billing documents for the authenticated user.

    Required query param: kind (devis | facture).
    Optional: status, project_id, limit, offset.
    """
    kind_str = request.args.get("kind", "").strip()
    try:
        kind = BillingDocumentKind(kind_str)
    except ValueError:
        return _err("ValidationError", "Invalid or missing kind: must be 'devis' or 'facture'", 400)

    status_str = request.args.get("status")
    status = None
    if status_str:
        try:
            status = BillingDocumentStatus(status_str)
        except ValueError:
            return _err("ValidationError", f"Invalid status: {status_str!r}", 400)

    project_id = None
    project_id_str = request.args.get("project_id")
    if project_id_str:
        try:
            project_id = UUID(project_id_str)
        except ValueError:
            return _err("ValidationError", "Invalid project_id", 400)

    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return _err("ValidationError", "limit and offset must be integers", 400)

    user_id = UUID(get_jwt_identity())
    result = get_container().list_billing_documents_usecase.execute(
        user_id=user_id,
        kind=kind,
        status=status,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
    return jsonify(
        {
            "items": [_doc_to_json(d) for d in result.items],
            "total": result.total,
            "limit": result.limit,
            "offset": result.offset,
        }
    )


# ---------------------------------------------------------------------------
# Create billing document
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
def create_billing_document():
    """Create a new billing document (devis or facture)."""
    try:
        body = CreateBillingDocumentRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    inp = CreateBillingDocumentInput(
        user_id=user_id,
        kind=BillingDocumentKind(body.kind),
        recipient_name=body.recipient_name,
        items=_items_from_schema(body.items),
        project_id=body.project_id,
        recipient_address=body.recipient_address,
        recipient_email=str(body.recipient_email) if body.recipient_email else None,
        recipient_siret=body.recipient_siret,
        notes=body.notes,
        terms=body.terms,
        signature_block_text=body.signature_block_text,
        validity_until=body.validity_until,
        payment_due_date=body.payment_due_date,
        payment_terms=body.payment_terms,
        issue_date=body.issue_date,
    )

    from app import db

    try:
        result = get_container().create_billing_document_usecase.execute(inp, db.session)
    except MissingCompanyProfileError:
        return jsonify({"error": "Conflict", "reason": "company_profile_missing"}), 409
    except BillingNumberCollisionError:
        return _err("Conflict", "Document number collision, please retry", 409)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_doc_to_json(result)), 201


# ---------------------------------------------------------------------------
# Get billing document
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/<doc_id>", methods=["GET"])
@jwt_required()
@require_billing_document_owner
def get_billing_document(doc_id: str, billing_doc):
    """Retrieve a single billing document by ID (ownership enforced by decorator)."""
    from app.application.billing.dtos import BillingDocumentResponse

    return jsonify(_doc_to_json(BillingDocumentResponse.from_entity(billing_doc)))


# ---------------------------------------------------------------------------
# Update billing document
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/<doc_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_billing_document_owner
def update_billing_document(doc_id: str, billing_doc):
    """Partially update a billing document (immutable fields rejected by schema)."""
    try:
        body = UpdateBillingDocumentRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    inp = UpdateBillingDocumentInput(
        id=billing_doc.id,
        user_id=user_id,
        recipient_name=body.recipient_name,
        recipient_address=body.recipient_address,
        recipient_email=str(body.recipient_email) if body.recipient_email else None,
        recipient_siret=body.recipient_siret,
        items=_items_from_schema(body.items) if body.items is not None else None,
        notes=body.notes,
        terms=body.terms,
        signature_block_text=body.signature_block_text,
        validity_until=body.validity_until,
        payment_due_date=body.payment_due_date,
        payment_terms=body.payment_terms,
        project_id=body.project_id,
        issue_date=body.issue_date,
    )

    from app import db

    try:
        result = get_container().update_billing_document_usecase.execute(inp, db.session)
    except BillingDocumentNotFoundError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_doc_to_json(result))


# ---------------------------------------------------------------------------
# Delete billing document
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/<doc_id>", methods=["DELETE"])
@jwt_required()
@require_billing_document_owner
def delete_billing_document(doc_id: str, billing_doc):
    """Delete a billing document (ownership enforced by decorator)."""
    from app import db

    user_id = UUID(get_jwt_identity())
    try:
        get_container().delete_billing_document_usecase.execute(billing_doc.id, user_id, db.session)
    except BillingDocumentNotFoundError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)

    return "", 204


# ---------------------------------------------------------------------------
# Clone billing document
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/<doc_id>/clone", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
@require_billing_document_owner
def clone_billing_document(doc_id: str, billing_doc):
    """Clone an existing billing document into a new draft."""
    raw_body = request.get_json(force=True) or {}
    try:
        body = CloneRequest.model_validate(raw_body)
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    override_kind = BillingDocumentKind(body.override_kind) if body.override_kind else None
    inp = CloneBillingDocumentInput(
        source_id=billing_doc.id,
        user_id=user_id,
        override_kind=override_kind,
    )

    from app import db

    try:
        result = get_container().clone_billing_document_usecase.execute(inp, db.session)
    except BillingDocumentNotFoundError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except MissingCompanyProfileError:
        return jsonify({"error": "Conflict", "reason": "company_profile_missing"}), 409
    except BillingNumberCollisionError:
        return _err("Conflict", "Document number collision, please retry", 409)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_doc_to_json(result)), 201


# ---------------------------------------------------------------------------
# Convert devis → facture
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/<doc_id>/convert-to-facture", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
@require_billing_document_owner
def convert_to_facture(doc_id: str, billing_doc):
    """Convert an accepted devis to a new facture draft.

    Body is optional — accepts empty {}.
    """
    raw_body = request.get_json(force=True, silent=True) or {}
    try:
        body = ConvertRequest.model_validate(raw_body)
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    inp = ConvertDevisToFactureInput(
        source_devis_id=billing_doc.id,
        user_id=user_id,
        payment_due_date=body.payment_due_date,
        payment_terms=body.payment_terms,
    )

    from app import db

    try:
        result = get_container().convert_devis_to_facture_usecase.execute(inp, db.session)
    except BillingDocumentNotFoundError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except DevisAlreadyConvertedError:
        return _err("Conflict", "This devis was already converted to a facture", 409)
    except MissingCompanyProfileError:
        return jsonify({"error": "Conflict", "reason": "company_profile_missing"}), 409
    except BillingNumberCollisionError:
        return _err("Conflict", "Document number collision, please retry", 409)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_doc_to_json(result)), 201


# ---------------------------------------------------------------------------
# Update status
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/<doc_id>/status", methods=["PATCH"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_billing_document_owner
def update_billing_document_status(doc_id: str, billing_doc):
    """Transition a billing document to a new status."""
    try:
        body = UpdateStatusRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    inp = UpdateStatusInput(
        id=billing_doc.id,
        user_id=user_id,
        new_status=BillingDocumentStatus(body.new_status),
    )

    from app import db

    try:
        result = get_container().update_billing_document_status_usecase.execute(inp, db.session)
    except BillingDocumentNotFoundError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except InvalidStatusTransitionError as exc:
        return _err("Conflict", str(exc), 409)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_doc_to_json(result))


# ---------------------------------------------------------------------------
# Render PDF
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/<doc_id>/pdf", methods=["GET"])
@jwt_required()
@limiter.limit("5 per minute", key_func=jwt_user_key)
@require_billing_document_owner
def render_billing_document_pdf(doc_id: str, billing_doc):
    """Render billing document as PDF and stream as attachment.

    Content-Disposition filename is RFC-5987 percent-encoded to support
    non-ASCII characters (mirrors labor-export pattern).
    """
    user_id = UUID(get_jwt_identity())
    try:
        pdf_result = get_container().render_billing_document_pdf_usecase.execute(billing_doc.id, user_id)
    except BillingDocumentNotFoundError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing document {doc_id} not found", 404)

    # RFC-5987 percent-encoding for filenames that may contain non-ASCII chars
    ascii_name = pdf_result.filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    encoded_name = quote(pdf_result.filename, safe="")
    content_disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"

    response = send_file(
        BytesIO(pdf_result.content),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_result.filename,
    )
    response.headers["Content-Disposition"] = content_disposition
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------------------------------------------------------------------
# Apply template (creates a document pre-filled from a template)
# ---------------------------------------------------------------------------


@billing_documents_bp.route("/billing-documents/from-template/<template_id>", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
def create_document_from_template(template_id: str):
    """Create a new billing document pre-filled from a template."""
    try:
        body = ApplyTemplateRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    try:
        tpl_uuid = UUID(template_id)
    except ValueError:
        return _err("ValidationError", f"Invalid template_id: {template_id}", 422)

    user_id = UUID(get_jwt_identity())
    inp = ApplyTemplateInput(
        template_id=tpl_uuid,
        user_id=user_id,
        recipient_name=body.recipient_name,
        project_id=body.project_id,
        recipient_address=body.recipient_address,
        recipient_email=str(body.recipient_email) if body.recipient_email else None,
        recipient_siret=body.recipient_siret,
        issue_date=body.issue_date,
    )

    from app import db

    try:
        result = get_container().apply_template_usecase.execute(inp, db.session)
    except BillingTemplateNotFoundError:
        return _err("NotFound", f"Billing template {template_id} not found", 404)
    except ForbiddenBillingDocumentError:
        return _err("NotFound", f"Billing template {template_id} not found", 404)
    except MissingCompanyProfileError:
        return jsonify({"error": "Conflict", "reason": "company_profile_missing"}), 409
    except BillingNumberCollisionError:
        return _err("Conflict", "Document number collision, please retry", 409)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(_doc_to_json(result)), 201
