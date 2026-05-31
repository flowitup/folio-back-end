"""Bibliotheque API blueprint — company-scoped product library endpoints."""

from flask import Blueprint

bibliotheque_bp = Blueprint("bibliotheque", __name__)

from app.api.v1.bibliotheque import routes  # noqa: E402, F401
