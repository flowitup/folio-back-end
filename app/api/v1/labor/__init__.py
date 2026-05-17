"""Labor API blueprint."""

from flask import Blueprint

labor_bp = Blueprint("labor", __name__)

from app.api.v1.labor import worker_routes, entry_routes, labor_role_routes  # noqa: E402, F401
