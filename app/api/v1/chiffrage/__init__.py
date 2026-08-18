"""Chiffrage blueprint — project-scoped material provisioning endpoints."""

from flask import Blueprint

chiffrage_bp = Blueprint("chiffrage", __name__)

# Routes are imported for side-effects (decorator registration).
from app.api.v1.chiffrage import routes  # noqa: E402, F401
