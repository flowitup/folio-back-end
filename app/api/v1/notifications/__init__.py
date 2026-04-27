"""Notifications blueprint — user-scoped due-reminder endpoints."""

from flask import Blueprint

notifications_bp = Blueprint("notifications", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.notifications import routes  # noqa: E402, F401
