"""Projects API blueprint."""

from flask import Blueprint

projects_bp = Blueprint("projects", __name__, url_prefix="/projects")

from app.api.v1.projects import routes  # noqa: E402, F401
from app.api.v1.projects import assignment_routes  # noqa: E402,F401
