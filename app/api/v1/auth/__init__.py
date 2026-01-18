"""Auth API Blueprint."""

from flask import Blueprint

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

from app.api.v1.auth import routes  # noqa: E402, F401
