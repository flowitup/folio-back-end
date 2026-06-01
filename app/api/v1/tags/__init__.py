"""Tags blueprint — project-scoped phase tag CRUD + summary endpoints."""

from flask import Blueprint

tags_bp = Blueprint("tags", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.tags import routes  # noqa: E402, F401
