"""Companies API routes — 12 endpoints.

Decorator order (MANDATORY): @jwt_required() BEFORE @limiter.limit(...) BEFORE role checks.

Security notes:
  - Token redeem (attach-by-token) returns uniform 410 with reason discriminator
    regardless of failure mode; does NOT differentiate wrong/expired/redeemed.
  - GET /companies/<id> returns 404 (not 403) for non-attached callers.
"""

from __future__ import annotations

import dataclasses
from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.v1.companies import companies_bp, users_me_bp
from app.api.v1.companies.decorators import require_admin, require_attached_company
from app.api.v1.companies.schemas import (
    CreateCompanyRequest,
    RedeemInviteTokenRequest,
    SetPrimaryCompanyRequest,
    UpdateCompanyRequest,
)
from app.application.companies import (
    BootAttachedUserInput,
    CompanyResponse,
    CreateCompanyInput,
    DetachCompanyInput,
    GenerateInviteTokenInput,
    GetCompanyInput,
    ListAllCompaniesInput,
    ListAttachedUsersInput,
    RedeemInviteTokenInput,
    RevokeInviteTokenInput,
    SetPrimaryCompanyInput,
    UpdateCompanyInput,
    ActiveInviteTokenAlreadyExistsError,
    CompanyAlreadyAttachedError,
    CompanyNotFoundError,
    ForbiddenCompanyError,
    InviteTokenAlreadyRedeemedError,
    InviteTokenExpiredError,
    InviteTokenNotFoundError,
    MissingPrimaryCompanyError,
    UserCompanyAccessNotFoundError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(error: str, message: str, status: int) -> Tuple[Response, int]:
    return jsonify({"error": error, "message": message}), status


def _has_superadmin() -> bool:
    jwt_claims = get_jwt()
    return "*:*" in jwt_claims.get("permissions", [])


def _company_to_dict(dto: CompanyResponse) -> dict:
    return dataclasses.asdict(dto)


# ---------------------------------------------------------------------------
# GET /companies — list my companies (or all if ?scope=all + admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies", methods=["GET"])
@jwt_required()
def list_companies():
    """List companies.

    Default: returns companies the caller is attached to.
    ?scope=all: returns all companies (admin only).
    """
    caller_id = UUID(get_jwt_identity())
    scope = request.args.get("scope", "").strip()

    container = get_container()

    if scope == "all":
        if not _has_superadmin():
            return _err("Forbidden", "Admin permission required for ?scope=all", 403)
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
            offset = int(request.args.get("offset", 0))
        except ValueError:
            return _err("ValidationError", "limit and offset must be integers", 400)

        inp = ListAllCompaniesInput(caller_id=caller_id, limit=limit, offset=offset)
        result = container.list_all_companies_usecase.execute(inp)
        return jsonify(
            {
                "items": [_company_to_dict(c) for c in result.items],
                "total": result.total,
                "limit": limit,
                "offset": offset,
            }
        )

    # Default: list MY companies
    result = container.list_my_companies_usecase.execute(caller_id)
    items = [
        {
            "company": _company_to_dict(r.company),
            "access": dataclasses.asdict(r.access),
        }
        for r in result.items
    ]
    return jsonify({"items": items})


# ---------------------------------------------------------------------------
# POST /companies — create company (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
@require_admin
def create_company():
    """Create a new company (admin only)."""
    try:
        body = CreateCompanyRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    inp = CreateCompanyInput(
        caller_id=caller_id,
        legal_name=body.legal_name,
        address=body.address,
        siret=body.siret,
        tva_number=body.tva_number,
        iban=body.iban,
        bic=body.bic,
        logo_url=str(body.logo_url) if body.logo_url else None,
        default_payment_terms=body.default_payment_terms,
        prefix_override=body.prefix_override,
    )

    try:
        result = get_container().create_company_usecase.execute(inp)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return jsonify(_company_to_dict(result)), 201


# ---------------------------------------------------------------------------
# GET /companies/<company_id> — get company (admin or attached)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>", methods=["GET"])
@jwt_required()
def get_company(company_id: str):
    """Get a company by ID.

    Returns 404 for non-attached non-admin callers (avoids enumeration).
    """
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    is_admin = _has_superadmin()
    inp = GetCompanyInput(caller_id=caller_id, company_id=company_uuid, is_admin=is_admin)

    try:
        result = get_container().get_company_usecase.execute(inp)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    return jsonify(_company_to_dict(result))


# ---------------------------------------------------------------------------
# PUT /companies/<company_id> — update company (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>", methods=["PUT"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_admin
def update_company(company_id: str):
    """Update a company (admin only)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    try:
        body = UpdateCompanyRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    inp = UpdateCompanyInput(
        id=company_uuid,
        caller_id=caller_id,
        legal_name=body.legal_name,
        address=body.address,
        siret=body.siret,
        tva_number=body.tva_number,
        iban=body.iban,
        bic=body.bic,
        logo_url=str(body.logo_url) if body.logo_url else None,
        default_payment_terms=body.default_payment_terms,
        prefix_override=body.prefix_override,
    )

    try:
        result = get_container().update_company_usecase.execute(inp)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return jsonify(_company_to_dict(result))


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id> — delete company (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>", methods=["DELETE"])
@jwt_required()
@require_admin
def delete_company(company_id: str):
    """Delete a company (admin only)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    caller_id = UUID(get_jwt_identity())

    from app import db

    try:
        get_container().delete_company_usecase.execute(
            caller_id=caller_id,
            company_id=company_uuid,
            db_session=db.session,
        )
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return "", 204


# ---------------------------------------------------------------------------
# POST /companies/<company_id>/invite-tokens — generate invite token (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/invite-tokens", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
@require_admin
def generate_invite_token(company_id: str):
    """Generate an invite token for a company (admin only).

    ?regenerate=true atomically deletes the existing active token and creates a new one.
    Without the flag, returns 409 if an active token already exists.
    """
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    regenerate_str = request.args.get("regenerate", "false").lower()
    regenerate = regenerate_str in ("1", "true", "yes")

    caller_id = UUID(get_jwt_identity())
    inp = GenerateInviteTokenInput(
        company_id=company_uuid,
        caller_id=caller_id,
        regenerate=regenerate,
    )

    try:
        result = get_container().generate_invite_token_usecase.execute(inp)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)
    except ActiveInviteTokenAlreadyExistsError:
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": "An active invite token already exists. Use ?regenerate=true to replace it.",
                    "reason": "active_token_exists",
                }
            ),
            409,
        )

    return (
        jsonify(
            {
                "token": result.plaintext_token,
                "token_id": str(result.token_id),
                "expires_at": result.expires_at.isoformat(),
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id>/invite-tokens/active — revoke invite token (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/invite-tokens/active", methods=["DELETE"])
@jwt_required()
@require_admin
def revoke_invite_token(company_id: str):
    """Revoke the active invite token for a company (admin only)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    inp = RevokeInviteTokenInput(company_id=company_uuid, caller_id=caller_id)

    try:
        get_container().revoke_invite_token_usecase.execute(inp)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except InviteTokenNotFoundError:
        return _err("NotFound", "No active invite token found for this company", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return "", 204


# ---------------------------------------------------------------------------
# POST /companies/attach-by-token — redeem invite token (jwt)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/attach-by-token", methods=["POST"])
@jwt_required()
@limiter.limit("5 per minute", key_func=jwt_user_key)
def redeem_invite_token():
    """Attach the caller to a company by redeeming an invite token.

    Security: uniform 410 with reason discriminator to avoid leaking whether a
    token exists, is wrong, expired, or already redeemed. Wrong tokens (not found
    after hashing) → 410 reason=invalid. Expired/already-redeemed → 410 with
    specific reason. This prevents oracle attacks on token enumeration.
    """
    try:
        body = RedeemInviteTokenRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    inp = RedeemInviteTokenInput(user_id=caller_id, plaintext_token=body.token)

    from app import db

    try:
        get_container().redeem_invite_token_usecase.execute(inp, db.session)
    except InviteTokenNotFoundError:
        # Token not found after argon2 match attempt — uniform 410
        return jsonify({"error": "Gone", "reason": "invalid"}), 410
    except InviteTokenExpiredError:
        return jsonify({"error": "Gone", "reason": "expired"}), 410
    except InviteTokenAlreadyRedeemedError:
        return jsonify({"error": "Gone", "reason": "already_redeemed"}), 410
    except CompanyAlreadyAttachedError:
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": "You are already attached to this company.",
                    "reason": "already_attached",
                }
            ),
            409,
        )

    return jsonify({"status": "attached"}), 200


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id>/access — detach self from company (jwt + attached)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/access", methods=["DELETE"])
@jwt_required()
@require_attached_company()
def detach_company(company_id: str):
    """Detach the authenticated caller from a company (self-service)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    inp = DetachCompanyInput(user_id=caller_id, company_id=company_uuid)

    from app import db

    try:
        get_container().detach_company_usecase.execute(inp, db.session)
    except UserCompanyAccessNotFoundError:
        return _err("NotFound", "You are not attached to this company", 404)

    return "", 204


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id>/access/<target_user_id> — boot user (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/access/<target_user_id>", methods=["DELETE"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_admin
def boot_attached_user(company_id: str, target_user_id: str):
    """Remove a user from a company (admin only)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    try:
        target_uuid = UUID(target_user_id)
    except ValueError:
        return _err("NotFound", f"User {target_user_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    inp = BootAttachedUserInput(
        caller_id=caller_id,
        company_id=company_uuid,
        target_user_id=target_uuid,
    )

    from app import db

    try:
        get_container().boot_attached_user_usecase.execute(inp, db.session)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except UserCompanyAccessNotFoundError:
        return _err("NotFound", f"User {target_user_id} is not attached to this company", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return "", 204


# ---------------------------------------------------------------------------
# GET /companies/<company_id>/attached-users — list attached users (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/attached-users", methods=["GET"])
@jwt_required()
@require_admin
def list_attached_users(company_id: str):
    """List all users attached to a company (admin only)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    inp = ListAttachedUsersInput(caller_id=caller_id, company_id=company_uuid)

    try:
        result = get_container().list_attached_users_usecase.execute(inp)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return jsonify({"items": [dataclasses.asdict(r) for r in result]})


# ---------------------------------------------------------------------------
# PUT /users/me/primary-company — set primary company (jwt)
# ---------------------------------------------------------------------------


@users_me_bp.route("/users/me/primary-company", methods=["PUT"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
def set_primary_company():
    """Set the caller's primary company."""
    try:
        body = SetPrimaryCompanyRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    inp = SetPrimaryCompanyInput(user_id=caller_id, company_id=body.company_id)

    from app import db

    try:
        get_container().set_primary_company_usecase.execute(inp, db.session)
    except UserCompanyAccessNotFoundError:
        return _err("NotFound", "You are not attached to the specified company", 404)
    except MissingPrimaryCompanyError:
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": "No attached companies found. Attach to a company first.",
                    "reason": "no_attached_companies",
                }
            ),
            409,
        )

    return jsonify({"status": "ok"}), 200
