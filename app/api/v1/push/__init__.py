"""Push notification device registration."""

from flask import Blueprint

push_bp = Blueprint("push", __name__)

from app.api.v1.push import routes  # noqa: E402,F401
