"""D8 member-grants API routes — 3 endpoints.

  GET    /companies/<company_id>/members/<user_id>/grants
  PUT    /companies/<company_id>/members/<user_id>/grants
  DELETE /companies/<company_id>/members/<user_id>/grants

All three require `@require_company_role("admin")` (company admin of
`company_id`, or platform `*:*`) — the same decorator `routes.py` uses for
company-management endpoints. `ManageGrantsUseCase` re-checks the same guard
internally (defense in depth) and additionally validates the D8 whitelist,
target role, and project/company ownership — see
`app.application.company_persons.manage_grants_usecase`.

This module is NOT wired through `app/__init__.py`'s DI container (owned by
another slice of this phase) — `_build_usecase()` below constructs
`ManageGrantsUseCase` directly per-request from `db.session` (this module's
own repository) plus shared, already-wired container dependencies
(`user_company_access_repo`, `company_repo`, `authorization_service`),
mirroring `app.api.v1.projects.decorators._get_project_company_id_from_orm`
for the project → company_id lookup (the domain `Project` entity omits
`company_id`).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError

from app import db
from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.companies import companies_bp
from app.api.v1.companies.decorators import require_company_role
from app.api.v1.companies.grants_schemas import (
    MemberGrantRow,
    MemberGrantsListResponse,
    RemoveMemberGrantRequest,
    SetMemberGrantRequest,
)
from app.application.company_persons.grants_ports import MemberGrant
from app.application.company_persons.manage_grants_usecase import (
    CompanyNotFoundForGrantsError,
    ForbiddenGrantsCallerError,
    InvalidGrantEffectError,
    InvalidGrantPermissionError,
    ListGrantsInput,
    ManageGrantsUseCase,
    NonDeniablePermissionError,
    ProjectNotInCompanyError,
    RemoveGrantInput,
    SetGrantInput,
    TargetNotCompanyMemberError,
    TargetNotCustomisableError,
)
from app.infrastructure.database.repositories.sqlalchemy_company_member_grant_repository import (
    SqlAlchemyCompanyMemberGrantRepository,
)
from app.infrastructure.rate_limiter import limiter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(error: str, message: str, status: int):
    return jsonify({"error": error, "message": message}), status


def _parse_uuid(raw: str) -> Optional[UUID]:
    try:
        return UUID(raw)
    except (ValueError, TypeError):
        return None


class _OrmProjectCompanyResolver:
    """Resolves a project's `company_id` straight from the ORM row.

    The domain `Project` entity intentionally omits `company_id` (kept in the
    infrastructure layer) — see
    `app.api.v1.projects.decorators._get_project_company_id_from_orm`, whose
    rationale this mirrors exactly.
    """

    def company_id_for_project(self, project_id: UUID) -> Optional[UUID]:
        from app.infrastructure.database.models.project import ProjectModel

        row = db.session.get(ProjectModel, project_id)
        return row.company_id if row is not None else None


def _build_usecase() -> ManageGrantsUseCase:
    from app import db
    from wiring import get_container

    container = get_container()
    grant_repo = SqlAlchemyCompanyMemberGrantRepository(db.session)
    return ManageGrantsUseCase(
        grant_repo=grant_repo,
        access_repo=container.user_company_access_repo,
        company_repo=container.company_repo,
        project_resolver=_OrmProjectCompanyResolver(),
        role_checker=container.authorization_service,
    )


def _to_row(grant: MemberGrant) -> MemberGrantRow:
    return MemberGrantRow(
        permission=grant.permission,
        effect=grant.effect,
        project_id=grant.project_id,
        granted_at=grant.granted_at,
    )


_ERROR_STATUS = {
    ForbiddenGrantsCallerError: 403,
    CompanyNotFoundForGrantsError: 404,
    TargetNotCompanyMemberError: 404,
    ProjectNotInCompanyError: 404,
    InvalidGrantPermissionError: 400,
    InvalidGrantEffectError: 400,
    NonDeniablePermissionError: 400,
    TargetNotCustomisableError: 400,
}


def _map_error(exc: Exception) -> tuple[Response, int]:
    status = _ERROR_STATUS.get(type(exc), 400)
    error_name = "Forbidden" if status == 403 else ("NotFound" if status == 404 else "ValidationError")
    return _err(error_name, str(exc), status)


# ---------------------------------------------------------------------------
# GET /companies/<company_id>/members/<user_id>/grants
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/members/<user_id>/grants", methods=["GET"])
@openapi_doc(
    summary="List a company member's D8 grant/deny rows",
    responses={200: MemberGrantsListResponse},
    tags=["companies"],
)
@jwt_required()
@limiter.limit("60 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def list_member_grants(company_id: str, user_id: str):
    """List every D8 grant/deny row for a manager/member, plus the customisable whitelist."""
    company_uuid = _parse_uuid(company_id)
    target_uuid = _parse_uuid(user_id)
    if company_uuid is None or target_uuid is None:
        return _err("NotFound", "Invalid company or user id", 404)

    caller_id = UUID(get_jwt_identity())
    try:
        result = _build_usecase().list_grants(
            ListGrantsInput(caller_id=caller_id, company_id=company_uuid, user_id=target_uuid)
        )
    except Exception as exc:  # noqa: BLE001 — mapped to a specific status below
        if type(exc) not in _ERROR_STATUS:
            raise
        return _map_error(exc)

    return (
        jsonify(
            MemberGrantsListResponse(
                grants=[_to_row(g) for g in result.grants],
                customisable=result.customisable,
            ).model_dump(mode="json")
        ),
        200,
    )


# ---------------------------------------------------------------------------
# PUT /companies/<company_id>/members/<user_id>/grants
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/members/<user_id>/grants", methods=["PUT"])
@openapi_doc(
    summary="Grant or deny one permission to a company member (D8)",
    request=SetMemberGrantRequest,
    responses={200: MemberGrantRow},
    tags=["companies"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def set_member_grant(company_id: str, user_id: str):
    """Upsert a grant/deny row. Idempotent: setting a new effect on the same key replaces it."""
    company_uuid = _parse_uuid(company_id)
    target_uuid = _parse_uuid(user_id)
    if company_uuid is None or target_uuid is None:
        return _err("NotFound", "Invalid company or user id", 404)

    try:
        body = SetMemberGrantRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    try:
        grant = _build_usecase().set_grant(
            SetGrantInput(
                caller_id=caller_id,
                company_id=company_uuid,
                user_id=target_uuid,
                permission=body.permission,
                effect=body.effect,
                project_id=body.project_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 — mapped to a specific status below
        if type(exc) not in _ERROR_STATUS:
            raise
        return _map_error(exc)

    # The repository only flushes; the route owns the transaction boundary so the
    # row survives the request (flush alone is rolled back at teardown).
    db.session.commit()
    return jsonify(_to_row(grant).model_dump(mode="json")), 200


# ---------------------------------------------------------------------------
# DELETE /companies/<company_id>/members/<user_id>/grants
# ---------------------------------------------------------------------------


@companies_bp.route("/companies/<company_id>/members/<user_id>/grants", methods=["DELETE"])
@openapi_doc(
    summary="Remove a grant/deny row from a company member (D8)",
    request=RemoveMemberGrantRequest,
    tags=["companies"],
)
@jwt_required()
@limiter.limit("30 per minute", key_func=jwt_user_key)
@require_company_role("admin")
def remove_member_grant(company_id: str, user_id: str):
    """Delete a grant/deny row. 204 on success, 404 if no matching row existed."""
    company_uuid = _parse_uuid(company_id)
    target_uuid = _parse_uuid(user_id)
    if company_uuid is None or target_uuid is None:
        return _err("NotFound", "Invalid company or user id", 404)

    try:
        body = RemoveMemberGrantRequest.model_validate(request.get_json(force=True) or {})
    except ValidationError as exc:
        return format_validation_error(exc)

    caller_id = UUID(get_jwt_identity())
    try:
        removed = _build_usecase().remove_grant(
            RemoveGrantInput(
                caller_id=caller_id,
                company_id=company_uuid,
                user_id=target_uuid,
                permission=body.permission,
                project_id=body.project_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 — mapped to a specific status below
        if type(exc) not in _ERROR_STATUS:
            raise
        return _map_error(exc)

    if not removed:
        return _err("NotFound", "No matching grant/deny row found", 404)
    db.session.commit()
    return "", 204
