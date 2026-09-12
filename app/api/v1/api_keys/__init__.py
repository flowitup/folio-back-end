"""API keys blueprint — personal automation credentials for the authenticated user."""

from flask import Blueprint

api_keys_bp = Blueprint("api_keys", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.api_keys import routes  # noqa: E402,F401
