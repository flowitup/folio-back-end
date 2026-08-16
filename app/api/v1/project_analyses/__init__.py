"""Project analyses blueprint — project-scoped HTML report library endpoints."""

from flask import Blueprint

project_analyses_bp = Blueprint("project_analyses", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.project_analyses import routes  # noqa: E402, F401
