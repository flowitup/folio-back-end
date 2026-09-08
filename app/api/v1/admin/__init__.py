"""Admin API Blueprint — platform-ops endpoints (bulk project assignment, user search)."""

from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

from app.api.v1.admin import routes  # noqa: E402, F401
