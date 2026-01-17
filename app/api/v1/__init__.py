"""
API v1 Blueprint

This module defines the API v1 blueprint and registers all route handlers.
Routes are stubs returning 501 Not Implemented until fully implemented.
"""

from flask import Blueprint, jsonify

bp = Blueprint("api_v1", __name__)


@bp.route("/projects", methods=["GET"])
def list_projects():
    """List all projects - stub returning 501."""
    return (
        jsonify({"error": "Not Implemented", "message": "GET /projects is not yet implemented"}),
        501,
    )


@bp.route("/projects", methods=["POST"])
def create_project():
    """Create a new project - stub returning 501."""
    return (
        jsonify({"error": "Not Implemented", "message": "POST /projects is not yet implemented"}),
        501,
    )


@bp.route("/projects/<project_id>", methods=["GET"])
def get_project(project_id: str):
    """Get a project by ID - stub returning 501."""
    return (
        jsonify(
            {
                "error": "Not Implemented",
                "message": f"GET /projects/{project_id} is not yet implemented",
            }
        ),
        501,
    )


@bp.route("/projects/<project_id>", methods=["PUT"])
def update_project(project_id: str):
    """Update a project - stub returning 501."""
    return (
        jsonify(
            {
                "error": "Not Implemented",
                "message": f"PUT /projects/{project_id} is not yet implemented",
            }
        ),
        501,
    )


@bp.route("/projects/<project_id>", methods=["DELETE"])
def delete_project(project_id: str):
    """Delete a project - stub returning 501."""
    return (
        jsonify(
            {
                "error": "Not Implemented",
                "message": f"DELETE /projects/{project_id} is not yet implemented",
            }
        ),
        501,
    )


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
