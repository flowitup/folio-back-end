"""Deprecated roles endpoint.

Per-project and global roles no longer exist: what a user may do comes from
their company role (`admin` | `manager` | `member`) plus per-member grant/deny
rows. The endpoint is kept as an empty stub — and not removed — because
store-distributed mobile builds call it to populate an invite role picker; it
disappears once those builds are retired.
"""

from flask import jsonify
from flask_jwt_extended import jwt_required

from app.api.v1.roles import roles_bp
from app.infrastructure.rate_limiter import limiter


@roles_bp.route("", methods=["GET"])
@jwt_required()
@limiter.limit("60 per minute")
def list_roles():
    """Deprecated: always returns an empty role list."""
    return jsonify({"roles": [], "deprecated": True}), 200
