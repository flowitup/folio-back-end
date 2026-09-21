"""Assistant blueprint — actions on the Folio Assistant conversation (FEATURE_ASSISTANT)."""

from flask import Blueprint

assistant_bp = Blueprint("assistant", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.assistant import routes  # noqa: E402, F401
