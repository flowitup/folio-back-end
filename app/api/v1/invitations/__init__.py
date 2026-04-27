"""Invitations API Blueprint."""

from flask import Blueprint

invitations_bp = Blueprint("invitations", __name__)

from app.api.v1.invitations import routes  # noqa: E402, F401
