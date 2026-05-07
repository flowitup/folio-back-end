"""RBAC decorators for companies routes.

require_admin:
  - Checks caller has *:* wildcard permission.
  - 403 if not.

require_attached_company(company_id_kwarg):
  - Verifies caller has a UserCompanyAccess row for the given company,
    OR has *:* admin permission (admins can access any company).
  - 404 if company not found (avoids enumeration).
  - 403 if caller is not attached and not admin.

Decorator order on routes (MANDATORY):
  @jwt_required()
  @limiter.limit(...)      # optional
  @require_admin           # or @require_attached_company(...)
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
