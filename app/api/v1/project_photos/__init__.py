"""Project photos blueprint."""

from flask import Blueprint

project_photos_bp = Blueprint("project_photos", __name__)

from app.api.v1.project_photos import routes  # noqa: E402, F401
