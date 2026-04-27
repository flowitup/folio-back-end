"""Roles API Blueprint."""

from flask import Blueprint

roles_bp = Blueprint("roles", __name__)

from app.api.v1.roles import routes  # noqa: E402, F401
