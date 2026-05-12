"""Persons API blueprint.

Endpoints (Phase 1b-ii):
  GET  /persons?q=...&limit=N  → search_persons (typeahead)
  POST /persons                → create_person

All routes mounted under /api/v1 in app/__init__.py.

Subsequent phases will add: PATCH /persons/<id>, POST /persons/merge.
"""

from flask import Blueprint

persons_bp = Blueprint("persons", __name__)

# Import route module to register handlers. noqa for module-level-import-not-at-top
# (E402) and unused-import (F401) — this is the Flask blueprint pattern used
# across the codebase.
from app.api.v1.persons import routes  # noqa: E402, F401

__all__ = ["persons_bp"]
