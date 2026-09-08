"""RBAC decorators for companies routes.

require_admin:
  - Checks caller has *:* wildcard permission.
  - 403 if not.

require_attached_company(company_id_kwarg):
  - Verifies caller has a UserCompanyAccess row for the given company,
    OR has *:* admin permission (admins can access any company).
  - 404 if company not found (avoids enumeration).
  - 403 if caller is not attached and not admin.

require_company_role(role, company_id_kwarg):
  - Verifies caller's per-company role for the given company equals *role*,
    OR has *:* admin permission (platform admins bypass the company-role check).
  - 404 if company not found (avoids enumeration).
  - 403 if caller is not attached, or attached with a different role.
  - Used for company-management endpoints (members, invite tokens, join code)
    so a company admin no longer needs platform *:* rights.

Decorator order on routes (MANDATORY):
  @jwt_required()
  @limiter.limit(...)      # optional
  @require_admin           # or @require_attached_company(...) or @require_company_role(...)
  def my_route(...): ...
"""

from __future__ import annotations

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity


def _has_superadmin() -> bool:
    """Return True if the JWT carries the *:* wildcard permission."""
    jwt_claims = get_jwt()
    return "*:*" in jwt_claims.get("permissions", [])


def _forbidden(message: str):
    return jsonify({"error": "Forbidden", "message": message}), 403


def _not_found(message: str):
    return jsonify({"error": "NotFound", "message": message}), 404


def require_admin(fn):
    """Decorator: assert caller has *:* permission; 403 otherwise.

    Must be placed AFTER @jwt_required() in the decorator stack.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _has_superadmin():
            return _forbidden("Admin permission required")
        return fn(*args, **kwargs)

    return wrapper


def require_attached_company(company_id_kwarg: str = "company_id"):
    """Decorator factory: verify caller is attached to the target company.

    Loads the UserCompanyAccess row for (caller_id, company_id).
    Admins (*:*) bypass the access check.
    Returns 404 if the company does not exist (avoids enumeration).
    Returns 403 if neither attached nor admin.

    Usage::

        @companies_bp.route("/companies/<company_id>/access", methods=["DELETE"])
        @jwt_required()
        @require_attached_company()
        def detach_company(company_id: str): ...
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from wiring import get_container

            cid_str = kwargs.get(company_id_kwarg)
            if not cid_str:
                return _not_found(f"Missing {company_id_kwarg}")
            try:
                company_uuid = UUID(cid_str)
            except ValueError:
                return _not_found(f"Invalid company id: {cid_str!r}")

            caller_id = UUID(get_jwt_identity())
            container = get_container()

            # Admins bypass the access check but still verify company exists
            if _has_superadmin():
                company = container.company_repo.find_by_id(company_uuid)
                if company is None:
                    return _not_found(f"Company {cid_str} not found")
                return fn(*args, **kwargs)

            # Non-admin: must have an access row
            access = container.user_company_access_repo.find(caller_id, company_uuid)
            if access is None:
                # Check if company exists to give correct 404 vs 403
                company = container.company_repo.find_by_id(company_uuid)
                if company is None:
                    return _not_found(f"Company {cid_str} not found")
                return _forbidden(f"You are not attached to company {cid_str}")

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_company_role(role: str = "admin", company_id_kwarg: str = "company_id"):
    """Decorator factory: assert caller holds *role* in the target company, or is a platform admin.

    Company-scoped replacement for @require_admin on company-management routes
    (members, invite tokens, join code, ...): a company admin no longer needs
    the global *:* wildcard to manage their own company, but still gets 403
    when the role does not match — including when the caller is an admin of a
    *different* company (a plain access-row lookup, not `list_for_user`).

    Loads the UserCompanyAccess row for (caller_id, company_id) and compares
    its role string to *role* (no dependency on any fixed set of role names,
    so this keeps working if new company roles are added later).
    Returns 404 if the company does not exist (avoids enumeration).
    Returns 403 if the caller is not attached, or attached with a different role.

    Usage::

        @companies_bp.route("/companies/<company_id>", methods=["PUT"])
        @jwt_required()
        @require_company_role("admin")
        def update_company(company_id: str): ...
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from wiring import get_container

            cid_str = kwargs.get(company_id_kwarg)
            if not cid_str:
                return _not_found(f"Missing {company_id_kwarg}")
            try:
                company_uuid = UUID(cid_str)
            except ValueError:
                return _not_found(f"Invalid company id: {cid_str!r}")

            container = get_container()

            # Admins bypass the role check but still verify company exists
            if _has_superadmin():
                company = container.company_repo.find_by_id(company_uuid)
                if company is None:
                    return _not_found(f"Company {cid_str} not found")
                return fn(*args, **kwargs)

            company = container.company_repo.find_by_id(company_uuid)
            if company is None:
                return _not_found(f"Company {cid_str} not found")

            caller_id = UUID(get_jwt_identity())
            access = container.user_company_access_repo.find(caller_id, company_uuid)
            if access is None or access.role != role:
                return _forbidden(f"Company role {role!r} required for company {cid_str}")

            return fn(*args, **kwargs)

        return wrapper

    return decorator
