"""Billing API blueprints.

Two blueprints:
  billing_documents_bp  — /billing-documents (10 endpoints)
  billing_templates_bp  — /billing-document-templates (5 endpoints)

The legacy company_profile_bp (/company-profile) has been retired as part of
the companies module migration (review C2). The underlying company_profile table
was dropped in migration 2d9c35848b9b. All company data now lives in the
companies + user_company_access tables.

All routes live under the /api/v1 prefix registered in app/__init__.py.
"""

from flask import Blueprint

billing_documents_bp = Blueprint("billing_documents", __name__)
billing_templates_bp = Blueprint("billing_templates", __name__)

# Import route modules to register handlers on the blueprints above.
# noqa comments suppress E402 (module-level import not at top) and F401 (unused import).
from app.api.v1.billing import documents_routes  # noqa: E402, F401
from app.api.v1.billing import templates_routes  # noqa: E402, F401

__all__ = ["billing_documents_bp", "billing_templates_bp"]
