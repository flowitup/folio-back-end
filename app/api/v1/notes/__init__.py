"""Notes blueprint — project-scoped note CRUD endpoints."""

from flask import Blueprint

notes_bp = Blueprint("notes", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.notes import routes  # noqa: E402, F401
