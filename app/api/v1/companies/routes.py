"""Companies API routes — 14 endpoints.

Decorator order (MANDATORY): @jwt_required() BEFORE @limiter.limit(...) BEFORE role checks.

Security notes:
  - GET /companies/<id> returns 404 (not 403) for non-attached callers.
"""

from __future__ import annotations

import dataclasses
from typing import Tuple
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.ops_context import is_platform_ops
from app.api.v1.companies import companies_bp, users_me_bp
from app.api.v1.companies.decorators import require_admin, require_attached_company, require_company_role
from app.api.v1.companies.schemas import (
    AttachedUsersListResponse,
    AttachUserToCompanyResponse,
    JoinCompanyRequest,
    JoinCodeResponse,
    CreateCompanyRequest,
    SetPrimaryCompanyRequest,
    UpdateCompanyRequest,
)
from app.application.companies import (
    AttachUserToCompanyInput,
    BootAttachedUserInput,
    CompanyResponse,
    CreateCompanyInput,
    DetachCompanyInput,
    GetCompanyInput,
    ListAllCompaniesInput,
    ListAttachedUsersInput,
    SetMemberRoleInput,
    SetPrimaryCompanyInput,
    UpdateCompanyInput,
    CompanyAlreadyAttachedError,
    CompanyHasProjectsError,
    CompanyNotFoundError,
    ForbiddenCompanyError,
    LastCompanyAdminError,
    MissingPrimaryCompanyError,
    TargetUserNotFoundError,
    UserCompanyAccessNotFoundError,
)
from app.application.companies.join_code_usecases import JoinCodeNotFoundError
from app.domain.companies.roles import CompanyRole
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(error: str, message: str, status: int) -> Tuple[Response, int]:
    return jsonify({"error": error, "message": message}), status


def _has_superadmin() -> bool:
    """True for platform ops (flowitup support), never for a company admin."""
    return is_platform_ops()


def _may_read_join_code(caller_id: UUID, company_id: UUID) -> bool:
    """H4: the join code lets anyone become a member — only a platform admin
    or an admin of THIS company (who manages it) may read it back. Managers
    and members never see it."""
    if _has_superadmin():
        return True
    access = get_container().user_company_access_repo.find(caller_id, company_id)
    return access is not None and access.role == "admin"


def _company_to_dict(dto: CompanyResponse, caller_id: UUID) -> dict:
    data = dataclasses.asdict(dto)
    if not _may_read_join_code(caller_id, dto.id):
        data.pop("join_code", None)
    return data


# ---------------------------------------------------------------------------
# GET /companies — list my companies (or all if ?scope=all + admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies", methods=["GET"])
@openapi_doc(summary="List companies", tags=["companies"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
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
                "items": [_company_to_dict(c, caller_id) for c in result.items],
                "total": result.total,
                "limit": limit,
                "offset": offset,
            }
        )

    # Default: list MY companies
    result = container.list_my_companies_usecase.execute(caller_id)
    items = [
        {
            "company": _company_to_dict(r.company, caller_id),
            "access": dataclasses.asdict(r.access),
        }
        for r in result.items
    ]
    return jsonify({"items": items})


# ---------------------------------------------------------------------------
# POST /companies — create company (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies", methods=["POST"])
@openapi_doc(
    summary="Create a new company (self-service — any authenticated user)",
    request=CreateCompanyRequest,
    tags=["companies"],
)
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
def create_company():
    """Create a new company (self-service).

    Any authenticated user may create a company — no platform `*:*` permission
    required (Phase 2 D1/goal 1). The caller is attached as the company's
    `admin`, `is_primary` when it is their first company, and the default
    labor role roster is seeded.
    """
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

    from app import db

    result = get_container().create_company_usecase.execute(inp, db.session)

    return jsonify(_company_to_dict(result, caller_id)), 201


# ---------------------------------------------------------------------------
# GET /companies/<company_id> — get company (admin or attached)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>", methods=["GET"])
@openapi_doc(summary="Get a company by ID", tags=["companies"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
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

    return jsonify(_company_to_dict(result, caller_id))


# ---------------------------------------------------------------------------
# PUT /companies/<company_id> — update company (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>", methods=["PUT"])
@openapi_doc(
    summary="Update a company (admin only)",
    request=UpdateCompanyRequest,
    tags=["companies"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def update_company(company_id: str):
    """Update a company (company admin or platform admin)."""
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

    from app import db

    try:
        result = get_container().update_company_usecase.execute(inp, db.session)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return jsonify(_company_to_dict(result, caller_id))


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id> — delete company (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>", methods=["DELETE"])
@openapi_doc(summary="Delete a company (admin only)", tags=["companies"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
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
    except CompanyHasProjectsError as exc:
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": str(exc),
                    "reason": "company_has_projects",
                    "project_count": exc.project_count,
                }
            ),
            409,
        )

    return "", 204


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id>/access — detach self from company (jwt + attached)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/access", methods=["DELETE"])
@openapi_doc(summary="Detach the authenticated caller from a company", tags=["companies"])
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
    except LastCompanyAdminError:
        return (
            jsonify({"error": "Conflict", "message": "Company must keep at least one admin", "reason": "last_admin"}),
            409,
        )

    return "", 204


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id>/access/<target_user_id> — boot user (admin)
# ---------------------------------------------------------------------------


def _notify_company_membership(event: str, *, user_id, actor_id, company_id, role=None) -> None:
    """Fire-and-forget; the notifier swallows its own failures."""
    notifier = get_container().membership_push_notifier
    if notifier is not None:
        notifier.notify(event, user_id=user_id, actor_id=actor_id, entity_id=company_id, role=role)


@companies_bp.route("/companies/<company_id>/access/<target_user_id>", methods=["DELETE"])
@openapi_doc(summary="Remove a user from a company (admin only)", tags=["companies"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def boot_attached_user(company_id: str, target_user_id: str):
    """Remove a user from a company (company admin or platform admin)."""
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
    except LastCompanyAdminError:
        return (
            jsonify({"error": "Conflict", "message": "Company must keep at least one admin", "reason": "last_admin"}),
            409,
        )
    except IntegrityError:
        # H1: concurrent boot / auto-promote race
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": "Concurrent operation detected. Please retry.",
                    "reason": "concurrent_boot",
                }
            ),
            409,
        )

    _notify_company_membership(
        "company_member_removed", user_id=target_uuid, actor_id=caller_id, company_id=company_uuid
    )
    return "", 204


# ---------------------------------------------------------------------------
# POST /companies/<company_id>/access/<target_user_id> — attach user (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/access/<target_user_id>", methods=["POST"])
@openapi_doc(
    summary="Attach an existing user account to a company as member (admin only)",
    responses={200: AttachUserToCompanyResponse},
    tags=["companies"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def attach_user_to_company(company_id: str, target_user_id: str):
    """Attach an existing user account to a company as `member` (admin or platform admin).

    D1: guarded by @require_company_role("admin") on the PATH company, so an
    admin may only attach people to companies they administer. Idempotent:
    already attached is a success, not a conflict — the existing row and its
    role come back untouched, never duplicated or changed. Silent like every
    other admin bulk-attach path (import, add-by-phone): no
    `company_member_added` push kind exists.
    """
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    try:
        target_uuid = UUID(target_user_id)
    except ValueError:
        return _err("NotFound", f"User {target_user_id} not found", 404)

    caller_id = UUID(get_jwt_identity())
    inp = AttachUserToCompanyInput(
        caller_id=caller_id,
        company_id=company_uuid,
        target_user_id=target_uuid,
    )

    from app import db

    try:
        result = get_container().attach_user_to_company_usecase.execute(inp, db.session)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except TargetUserNotFoundError:
        return _err("NotFound", f"User {target_user_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    return jsonify(dataclasses.asdict(result)), 200


# ---------------------------------------------------------------------------
# PATCH /companies/<company_id>/access/<target_user_id>/role — set member role (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/access/<target_user_id>/role", methods=["PATCH"])
@openapi_doc(summary="Change a company member's role (admin only)", tags=["companies"])
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def set_member_role(company_id: str, target_user_id: str):
    """Promote/demote a company member's role (company admin or platform admin)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    try:
        target_uuid = UUID(target_user_id)
    except ValueError:
        return _err("NotFound", f"User {target_user_id} not found", 404)

    body = request.get_json(force=True, silent=True) or {}
    role = body.get("role")
    if not role:
        return _err("ValidationError", "role is required", 400)

    caller_id = UUID(get_jwt_identity())
    inp = SetMemberRoleInput(
        caller_id=caller_id,
        company_id=company_uuid,
        user_id=target_uuid,
        role=role,
    )

    from app import db

    try:
        result = get_container().set_member_role_usecase.execute(inp, db.session)
    except ValueError as exc:
        return _err("ValidationError", str(exc), 400)
    except UserCompanyAccessNotFoundError:
        return _err("NotFound", f"User {target_user_id} is not attached to this company", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)
    except LastCompanyAdminError:
        return (
            jsonify({"error": "Conflict", "message": "Company must keep at least one admin", "reason": "last_admin"}),
            409,
        )

    _notify_company_membership(
        "company_member_role_changed",
        user_id=target_uuid,
        actor_id=caller_id,
        company_id=company_uuid,
        role=getattr(result, "role", None),
    )
    return jsonify(dataclasses.asdict(result))


# ---------------------------------------------------------------------------
# Join code — shared short code, reusable until revoked, attaches as member
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/join-code", methods=["POST"])
@openapi_doc(
    summary="Create or renew the company's join code (admin only)",
    responses={200: JoinCodeResponse},
    tags=["companies"],
)
@jwt_required()
@limiter.limit("20 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def set_join_code(company_id: str):
    """Issue a new 8-character join code (replaces the previous one)."""
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    caller_id = UUID(get_jwt_identity())
    from app import db

    try:
        code = get_container().set_join_code_usecase.execute(company_uuid, True, db.session, caller_id=caller_id)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)
    return jsonify(JoinCodeResponse(join_code=code or "").model_dump()), 200


@companies_bp.route("/companies/<company_id>/join-code", methods=["DELETE"])
@openapi_doc(summary="Revoke the company's join code (admin only)", tags=["companies"])
@jwt_required()
@limiter.limit("20 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def revoke_join_code(company_id: str):
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    caller_id = UUID(get_jwt_identity())
    from app import db

    try:
        get_container().set_join_code_usecase.execute(company_uuid, False, db.session, caller_id=caller_id)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)
    return "", 204


@companies_bp.route("/companies/join", methods=["POST"])
@openapi_doc(summary="Join a company as member with its join code", request=JoinCompanyRequest, tags=["companies"])
@jwt_required()
@limiter.limit("10 per minute", key_func=jwt_user_key)
def join_company_by_code():
    """Attach the caller to the company owning the code (role member; primary when it is their first)."""
    try:
        body = JoinCompanyRequest(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        # format_validation_error already returns a (response, status) pair — wrapping it in
        # _err() would embed a Response inside a dict and make jsonify raise (500 instead of 422).
        return format_validation_error(exc)
    caller_id = UUID(get_jwt_identity())
    from app import db

    try:
        result = get_container().join_company_by_code_usecase.execute(caller_id, body.code, db.session)
    except JoinCodeNotFoundError:
        return _err("NotFound", "Unknown or revoked company code", 404)
    except CompanyAlreadyAttachedError:
        return _err("Conflict", "You already belong to this company", 409)
    return jsonify(_company_to_dict(result, caller_id)), 200


# ---------------------------------------------------------------------------
# GET /companies/<company_id>/attached-users — list attached users (admin)
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/attached-users", methods=["GET"])
@openapi_doc(
    summary="List users attached to a company (company admin)",
    responses={200: AttachedUsersListResponse},
    tags=["companies"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def list_attached_users(company_id: str):
    """List users attached to a company (company admin or platform admin).

    Supports pagination via ?limit (default 50, max 200) and ?offset (default 0).
    Returns { items: [...], total: int }.
    """
    try:
        company_uuid = UUID(company_id)
    except ValueError:
        return _err("NotFound", f"Company {company_id} not found", 404)

    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return _err("ValidationError", "limit and offset must be integers", 400)

    caller_id = UUID(get_jwt_identity())
    inp = ListAttachedUsersInput(caller_id=caller_id, company_id=company_uuid, limit=limit, offset=offset)

    try:
        result = get_container().list_attached_users_usecase.execute(inp)
    except CompanyNotFoundError:
        return _err("NotFound", f"Company {company_id} not found", 404)
    except ForbiddenCompanyError:
        return _err("Forbidden", "Admin permission required", 403)

    items = [dataclasses.asdict(r) for r in result.items]
    _attach_user_identity(items)
    _attach_user_companies(items, caller_id)
    _attach_user_assignments(items, company_uuid)
    return jsonify({"items": items, "total": result.total})


def _attach_user_identity(items: list[dict]) -> None:
    """Add `email` / `display_name` / `phone` to attached-user rows (one batch query).

    The use case returns access rows only (user_id, role, …); clients render the
    member list and need a human label without a second round-trip per row.
    """
    from app import db
    from app.infrastructure.database.models import UserModel

    ids = [row["user_id"] for row in items]
    if not ids:
        return
    users = {u.id: u for u in db.session.query(UserModel).filter(UserModel.id.in_(ids)).all()}
    for row in items:
        user = users.get(row["user_id"])
        row["email"] = user.email if user else None
        row["display_name"] = (user.display_name if user else None) or (user.email.split("@", 1)[0] if user else None)
        row["phone"] = user.phone if user else None


def _attach_user_assignments(items: list[dict], company_id: UUID) -> None:
    """Add `assigned_project_ids` to attached-user rows (one batch query).

    The directory carries this for people who have a `company_persons`
    profile, but an account attached without one (every pre-Phase-2 user,
    and any path that writes the access row directly) is absent from it —
    so a member list that sourced assignments from the directory alone
    would show "no projects" for a user who is in fact assigned.
    """
    for row in items:
        row["assigned_project_ids"] = []

    ids = [row["user_id"] for row in items]
    if not ids:
        return

    reader = get_container().authz_reader
    if reader is None:
        return

    by_user = reader.assigned_project_ids_for_users(company_id, ids)
    for row in items:
        row["assigned_project_ids"] = [str(pid) for pid in by_user.get(row["user_id"], [])]


def _attach_user_companies(items: list[dict], caller_id: UUID) -> None:
    """Add `companies: [{id, legal_name}]` to attached-user rows (one batch query).

    D4 (no cross-tenant leak): each row lists the target's OWN company
    attachments intersected with the companies the CALLER administers — a
    company the target belongs to that the caller does not administer is
    neither shown nor counted here.
    """
    for row in items:
        row["companies"] = []

    ids = [row["user_id"] for row in items]
    if not ids:
        return

    caller_admin_company_ids = [
        access.company_id
        for access in get_container().user_company_access_repo.list_for_user(caller_id)
        if access.role == CompanyRole.ADMIN.value
    ]
    if not caller_admin_company_ids:
        return

    from app import db
    from app.infrastructure.database.models import CompanyModel, UserCompanyAccessModel

    rows = (
        db.session.query(UserCompanyAccessModel.user_id, CompanyModel.id, CompanyModel.legal_name)
        .join(CompanyModel, CompanyModel.id == UserCompanyAccessModel.company_id)
        .filter(
            UserCompanyAccessModel.user_id.in_(ids),
            UserCompanyAccessModel.company_id.in_(caller_admin_company_ids),
        )
        .all()
    )
    by_user: dict[UUID, list[dict]] = {}
    for user_id, company_id, legal_name in rows:
        by_user.setdefault(user_id, []).append({"id": str(company_id), "legal_name": legal_name})

    for row in items:
        row["companies"] = by_user.get(row["user_id"], [])


# ---------------------------------------------------------------------------
# PUT /users/me/primary-company — set primary company (jwt)
# ---------------------------------------------------------------------------


@users_me_bp.route("/users/me/primary-company", methods=["PUT"])
@openapi_doc(
    summary="Set the caller's primary company",
    request=SetPrimaryCompanyRequest,
    tags=["companies"],
)
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
    except IntegrityError:
        # H1: concurrent set-primary race — partial-unique index fired
        return (
            jsonify(
                {
                    "error": "Conflict",
                    "message": "Concurrent primary change detected. Please retry.",
                    "reason": "concurrent_primary_change",
                }
            ),
            409,
        )

    return jsonify({"status": "ok"}), 200
