"""Labor API blueprint."""

from flask import Blueprint

labor_bp = Blueprint("labor", __name__)

from app.api.v1.labor import (  # noqa: E402, F401
    worker_routes,
    entry_routes,
    labor_role_routes,
    activity_routes,
    rate_change_routes,
)
