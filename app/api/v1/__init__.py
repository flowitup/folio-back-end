"""
API v1 Blueprint

This module defines the API v1 blueprint and registers all route handlers.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

bp = Blueprint("api_v1", __name__)


@bp.route("/users", methods=["GET"])
@jwt_required()
def list_users():
    """Search users by email query parameter."""
    from wiring import get_container

    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"users": [], "total": 0})

    container = get_container()
    users = container.user_repository.search_by_email(query, limit=10)

    return jsonify({"users": [{"id": str(u[0]), "email": u[1]} for u in users], "total": len(users)})


@bp.route("/users/<user_id>", methods=["GET"])
def get_user(user_id: str):
    """Get a user by ID - stub returning 501."""
    return (
        jsonify({"error": "Not Implemented", "message": f"GET /users/{user_id} is not yet implemented"}),
        501,
    )
