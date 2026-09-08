"""Company member onboarding routes — add by phone, import, directory.

Endpoints (3):
  POST /companies/<id>/members          → add_member_by_phone (admin)
  POST /companies/<id>/members/import   → import_members (admin)
  GET  /companies/<id>/persons          → list_directory (admin or manager)

D8 grant/deny management routes live in a separate module
(`app.api.v1.companies.grants_routes`, a parallel slice) — not here.
"""

from __future__ import annotations

from uuid import UUID

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.companies import companies_bp
from app.api.v1.companies.decorators import require_company_role, require_company_role_any
from app.api.v1.companies.schemas import AddMemberByPhoneRequest, ImportMembersRequest
from app.application.companies import CompanyNotFoundError, ForbiddenCompanyError
from app.application.company_persons import (
    AddMemberByPhoneInput,
    AdminRoleNotAssignableError,
    ImportMembersInput,
    InvalidCandidatePersonError,
    MemberAlreadyAttachedError,
    MultipleCandidatesError,
    SourceCompanyNotAccessibleError,
)
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


def _err(error: str, message: str, status: int):
    return jsonify({"error": error, "message": message}), status


def _company_uuid_or_404(company_id: str):
    try:
        return UUID(company_id), None
    except ValueError:
        return None, _err("NotFound", f"Company {company_id} not found", 404)


# ---------------------------------------------------------------------------
# POST /companies/<company_id>/members — add by phone (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/members", methods=["POST"])
@openapi_doc(
    summary="Add a company member by phone number (admin only)",
    request=AddMemberByPhoneRequest,
    tags=["companies"],
)
@jwt_required()
@limiter.limit("20 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def add_member_by_phone(company_id: str):
    """Onboard a person by phone (match order: existing account → un-linked
    profile in another company the caller admins → several candidates (409)
    → brand new pending profile). See `AddMemberByPhoneUseCase`."""
    company_uuid, err = _company_uuid_or_404(company_id)
    if err is not None:
        return err

    try:
        body = AddMemberByPhoneRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    inp = AddMemberByPhoneInput(
        caller_id=caller_id,
        company_id=company_uuid,
        phone=body.phone,
        name=body.name,
        role=body.role,
        person_id=body.person_id,
    )

    from app import db

    try:
        result = get_container().add_member_by_phone_usecase.execute(inp, db.session)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)
    except AdminRoleNotAssignableError as exc:
        return _err("ValidationError", str(exc), 400)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)
    except MemberAlreadyAttachedError as exc:
        return _err("Conflict", str(exc), 409)
    except InvalidCandidatePersonError as exc:
        return _err("ValidationError", str(exc), 400)
    except MultipleCandidatesError as exc:
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": "Several people match this phone number — resend with person_id.",
                    "candidates": exc.candidates,
                }
            ),
            409,
        )

    return (
        jsonify(
            {
                "person_id": str(result.person_id),
                "name": result.name,
                "phone": result.phone,
                "pending": result.pending,
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# POST /companies/<company_id>/members/import — import from another company (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/members/import", methods=["POST"])
@openapi_doc(
    summary="Import member profiles from another company the caller also admins",
    request=ImportMembersRequest,
    tags=["companies"],
)
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def import_members(company_id: str):
    """Copy profiles (no pay data) from `from_company_id` into this company;
    attaches already-linked user accounts as `member`. Caller must be admin
    of both companies."""
    company_uuid, err = _company_uuid_or_404(company_id)
    if err is not None:
        return err

    try:
        body = ImportMembersRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    inp = ImportMembersInput(
        caller_id=caller_id,
        company_id=company_uuid,
        from_company_id=body.from_company_id,
        person_ids=list(body.person_ids),
    )

    from app import db

    try:
        result = get_container().import_members_usecase.execute(inp, db.session)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)
    except SourceCompanyNotAccessibleError:
        return _err("Forbidden", "Admin permission required in the source company", 403)

    return (
        jsonify(
            {
                "items": [
                    {
                        "person_id": str(item.person_id),
                        "name": item.name,
                        "phone": item.phone,
                        "linked_user_id": str(item.linked_user_id) if item.linked_user_id else None,
                    }
                    for item in result.items
                ]
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# GET /companies/<company_id>/persons — directory (admin or manager)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/persons", methods=["GET"])
@openapi_doc(summary="List a company's member directory (admin or manager)", tags=["companies"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role_any("admin", "manager")
def list_directory(company_id: str):
    """Company-scoped person directory: `assigned_project_ids` is limited to
    THIS company's projects even for a person who also works elsewhere."""
    company_uuid, err = _company_uuid_or_404(company_id)
    if err is not None:
        return err

    result = get_container().list_directory_usecase.execute(company_uuid)

    return jsonify(
        {
            "items": [
                {
                    "person_id": str(entry.person_id),
                    "name": entry.name,
                    "phone": entry.phone,
                    "linked_user_id": str(entry.linked_user_id) if entry.linked_user_id else None,
                    "assigned_project_ids": [str(pid) for pid in entry.assigned_project_ids],
                    "is_active": entry.is_active,
                    "pending": entry.pending,
                    "labor_role_id": str(entry.labor_role_id) if entry.labor_role_id else None,
                    "default_daily_rate": (
                        float(entry.default_daily_rate) if entry.default_daily_rate is not None else None
                    ),
                }
                for entry in result.items
            ]
        }
    )
