"""
API v1 Blueprint

This module defines the API v1 blueprint and registers all route handlers.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

bp = Blueprint("api_v1", __name__)


@bp.route("/users", methods=["GET"])
@jwt_required()
def list_users():
    """Search users by email query parameter.

    Company admins and managers find users who share one of their companies, so one
    tenant cannot enumerate another tenant's accounts and a plain member cannot list
    colleagues. Platform ops search every user. Phone sign-ups' placeholder addresses
    (they embed the number) are neither matched nor returned.
    """
    from uuid import UUID

    from app.api.v1.ops_context import is_platform_ops
    from app.domain.companies.roles import CompanyRole
    from wiring import get_container

    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"users": [], "total": 0})

    caller_id = UUID(str(get_jwt_identity()))
    container = get_container()
    scope = None if is_platform_ops(caller_id) else caller_id
    roles = (CompanyRole.ADMIN.value, CompanyRole.MANAGER.value)
    access_repo = container.user_company_access_repo
    if scope is not None and (
        access_repo is None or not any(a.role in roles for a in access_repo.list_for_user(scope))
    ):
        return jsonify({"error": "Forbidden", "message": "Access denied", "status_code": 403}), 403
    users = container.user_repository.search_by_email(
        query, limit=10, sharing_company_with=scope, caller_roles=None if scope is None else roles
    )

    return jsonify({"users": [{"id": str(u[0]), "email": u[1]} for u in users], "total": len(users)})
