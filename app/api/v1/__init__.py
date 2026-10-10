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

    Results are limited to users who share a company with the caller, so one
    tenant cannot enumerate another tenant's accounts. Platform ops search
    every user.
    """
    from uuid import UUID

    from app.api.v1.ops_context import is_platform_ops
    from wiring import get_container

    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"users": [], "total": 0})

    caller_id = UUID(str(get_jwt_identity()))
    scope = None if is_platform_ops(caller_id) else caller_id
    container = get_container()
    users = container.user_repository.search_by_email(query, limit=10, sharing_company_with=scope)

    return jsonify({"users": [{"id": str(u[0]), "email": u[1]} for u in users], "total": len(users)})
