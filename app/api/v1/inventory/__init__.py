"""Inventory API blueprint — company-scoped equipment inventory endpoints."""

from flask import Blueprint

inventory_bp = Blueprint("inventory", __name__)

from app.api.v1.inventory import routes  # noqa: E402, F401
