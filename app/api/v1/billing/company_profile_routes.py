"""Company profile API routes.

Endpoints (2):
  GET /company-profile   → get (jwt, returns 404 if not configured yet)
  PUT /company-profile   → upsert (jwt, 30/min)

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
from app.api.v1.billing import company_profile_bp
from app.api.v1.billing.schemas import UpsertCompanyProfileRequest
from app.application.billing import UpsertCompanyProfileInput
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(error: str, message: str, status: int) -> Tuple[Response, int]:
    return jsonify({"error": error, "message": message}), status


# ---------------------------------------------------------------------------
# Get company profile
# ---------------------------------------------------------------------------


@company_profile_bp.route("/company-profile", methods=["GET"])
@jwt_required()
def get_company_profile():
    """Return the authenticated user's company profile.

    Returns 404 if no profile has been configured yet (FE should redirect to
    the company-profile settings page before attempting to create billing docs).
    """
    user_id = UUID(get_jwt_identity())
    result = get_container().get_company_profile_usecase.execute(user_id)
    if result is None:
        return _err("NotFound", "No company profile configured for this account", 404)
    return jsonify(dataclasses.asdict(result))


# ---------------------------------------------------------------------------
# Upsert company profile
# ---------------------------------------------------------------------------


@company_profile_bp.route("/company-profile", methods=["PUT"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def upsert_company_profile():
    """Create or fully replace the company profile for the authenticated user."""
    try:
        body = UpsertCompanyProfileRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    user_id = UUID(get_jwt_identity())
    inp = UpsertCompanyProfileInput(
        user_id=user_id,
        legal_name=body.legal_name,
        address=body.address,
        siret=body.siret,
        tva_number=body.tva_number,
        iban=body.iban,
        bic=body.bic,
        logo_url=body.logo_url,
        default_payment_terms=body.default_payment_terms,
        prefix_override=body.prefix_override,
    )

    from app import db

    try:
        result = get_container().upsert_company_profile_usecase.execute(inp, db.session)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)

    return jsonify(dataclasses.asdict(result))
