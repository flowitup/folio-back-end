"""
API v1 Blueprint

This module defines the API v1 blueprint and registers all route handlers.
"""

from flask import Blueprint, jsonify

bp = Blueprint("api_v1", __name__)


@bp.route("/users", methods=["GET"])
def list_users():
    """List all users - stub returning 501."""
    return (
        jsonify({"error": "Not Implemented", "message": "GET /users is not yet implemented"}),
        501,
    )


@bp.route("/users/<user_id>", methods=["GET"])
def get_user(user_id: str):
    """Get a user by ID - stub returning 501."""
    return (
        jsonify(
            {"error": "Not Implemented", "message": f"GET /users/{user_id} is not yet implemented"}
        ),
        501,
    )
