"""Roles API routes."""

from flask import jsonify
from flask_jwt_extended import jwt_required

from app.api.v1.roles import roles_bp
from app.infrastructure.rate_limiter import limiter
from wiring import get_container


@roles_bp.route("", methods=["GET"])
@jwt_required()
@limiter.limit("60 per minute")
def list_roles():
    """Return all roles excluding superadmin. Used by invite UI to populate role picker."""
    container = get_container()

    if not hasattr(container, "role_repository") or container.role_repository is None:
        return (
            jsonify({"error": "ServiceUnavailable", "message": "Role service not configured.", "status_code": 503}),
            503,
        )

    roles = container.role_repository.list_all()
    visible = [r for r in roles if r.name != "superadmin"]

    return (
        jsonify({"roles": [{"id": str(r.id), "name": r.name, "description": r.description or ""} for r in visible]}),
        200,
    )
